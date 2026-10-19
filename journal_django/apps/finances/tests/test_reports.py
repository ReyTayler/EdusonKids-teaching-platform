"""
Тесты сборки реестра признания выручки
(apps/finances/reports.py::collect_monthly_report).

Строка отчёта = платёж. Фикстуры — apps/finances/tests/conftest.py; БД общая
(managed=False), поэтому свои строки ищем по payment_id.

Спека: docs/superpowers/specs/2026-09-08-accounting-payment-ledger-design.md
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.finances.reports import collect_monthly_report

pytestmark = pytest.mark.django_db


def _add_payment(created, student_id, direction_id, subs, total, paid_at, kind='purchase'):
    # purchase/extra — положительные (extra = доплата за доп.урок сверх курса);
    # refund — отрицательные.
    positive = kind in ('purchase', 'extra')
    lessons = subs * 4 if positive else -(subs * 4)
    amount = total if positive else -total
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'test') RETURNING id",
            [student_id, direction_id, subs, lessons, kind, total, amount, paid_at],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


def _add_payment_exact(created, student_id, direction_id, lessons_count, unit_price, paid_at):
    """Оплата с явным числом уроков/ценой (в отличие от _add_payment, где lessons=subs*4)."""
    total = lessons_count * unit_price
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,1,%s,'purchase',%s,%s,%s,'test') RETURNING id",
            [student_id, direction_id, lessons_count, unit_price, total, paid_at],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


def _add_surcharge(created, student_id, parent_id, index, total, paid_at):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, subscriptions_count, lessons_count, kind, "
            "unit_price, total_amount, paid_at, created_by, parent_payment_id, subscription_index) "
            "VALUES (%s,NULL,NULL,'surcharge',%s,%s,%s,'test',%s,%s) RETURNING id",
            [student_id, total, total, paid_at, parent_id, index],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


def _add_lesson_attendance(created, group_id, teacher_id, student_id, date,
                           duration=60, is_free=False):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,%s,1,%s,'regular','test') RETURNING id",
            [group_id, teacher_id, date, duration],
        )
        lid = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
            'VALUES (%s,%s,true,%s)',
            [lid, student_id, is_free],
        )
    created['lessons'].append(lid)
    return lid


def _row(report, payment_id):
    return next((r for r in report.rows if r.payment_id == payment_id), None)


def test_row_shows_payment_recognition_by_month_total_and_advance(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    with connection.cursor() as cur:
        cur.execute("UPDATE students SET platform_id = 'PL-42' WHERE id = %s", [student_fixture])
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-05-01')
    # По одному уроку в мае, июне и июле — признание растянуто на три месяца.
    for date in ('2026-05-10', '2026-06-10', '2026-07-10'):
        _add_lesson_attendance(
            graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, date,
        )

    report = collect_monthly_report('2026-07')
    row = _row(report, pid)

    assert row is not None
    assert row.platform_id == 'PL-42'
    assert row.paid_at == '2026-05-01'
    assert row.total_amount == Decimal('2000')
    assert row.surcharge_amount == Decimal('0')
    assert row.unit_price == Decimal('500.00')
    assert row.revenue_by_month == {
        '2026-05': Decimal('500.00'),
        '2026-06': Decimal('500.00'),
        '2026-07': Decimal('500.00'),
    }
    assert row.revenue_total == Decimal('1500.00')
    assert row.refunded == Decimal('0.00')
    assert row.advance == Decimal('500.00')
    # Шкала месяцев — сплошная, от раннего признания до выбранного месяца.
    assert report.months[0] <= '2026-05'
    assert report.months[-1] == '2026-07'
    assert report.months == sorted(set(report.months))


def test_payment_received_in_month_appears_even_without_recognition(
    student_fixture, direction_fixture, graph_cleanup,
):
    """Деньги пришли в месяце — строка есть, даже если уроков по ним ещё не было."""
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-07-05')

    row = _row(collect_monthly_report('2026-07'), pid)

    assert row is not None
    assert row.revenue_by_month == {}
    assert row.revenue_total == Decimal('0.00')
    assert row.advance == Decimal('2000')          # вся сумма ещё авансом


def test_old_payment_without_recognition_in_month_is_absent(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Оплата прошлого месяца без признания в выбранном — не наше событие."""
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-05-05')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-05-10',
    )

    assert _row(collect_monthly_report('2026-07'), pid) is None


def test_surcharge_received_in_month_pulls_in_its_parent_payment(
    student_fixture, direction_fixture, graph_cleanup,
):
    """Доплата своей строки не имеет — значит, втягивает в отчёт родительскую оплату."""
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-05-05')
    _add_surcharge(graph_cleanup, student_fixture, pid, 1, 400, '2026-07-02')

    row = _row(collect_monthly_report('2026-07'), pid)

    assert row is not None
    assert row.surcharge_amount == Decimal('400')
    assert row.revenue_total == Decimal('0.00')
    assert row.advance == Decimal('2400')          # 2000 оплаты + 400 доплаты


def test_refund_alone_does_not_create_a_row(
    student_fixture, direction_fixture, graph_cleanup,
):
    """Возврат — не поступление: своей строкой он не становится."""
    refund_id = _add_payment(
        graph_cleanup, student_fixture, direction_fixture, 1, 1500, '2026-07-20', kind='refund',
    )

    assert _row(collect_monthly_report('2026-07'), refund_id) is None


def test_advance_is_as_of_end_of_month(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Уроки ПОСЛЕ выбранного месяца не уменьшают аванс и не создают колонок."""
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-06-01')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10',
    )
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-08-05',
    )

    report = collect_monthly_report('2026-07')
    row = _row(report, pid)

    assert row.revenue_total == Decimal('500.00')      # августовский урок не признан
    assert row.advance == Decimal('1500.00')           # 3 урока по 500 ещё авансом
    assert '2026-08' not in row.revenue_by_month
    assert report.months[-1] == '2026-07'


def test_surcharge_is_shown_in_its_own_column_and_raises_unit_price(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Доплата к абонементу своей строки не даёт: она видна в колонке родителя."""
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-07-01')
    sid = _add_surcharge(graph_cleanup, student_fixture, pid, 1, 400, '2026-07-02')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10',
    )

    report = collect_monthly_report('2026-07')
    row = _row(report, pid)

    assert _row(report, sid) is None                   # сама доплата строкой не является
    assert row.total_amount == Decimal('2000')
    assert row.surcharge_amount == Decimal('400')
    assert row.unit_price == Decimal('600.00')         # (2000 + 400) / 4
    assert row.revenue_total == Decimal('600.00')      # 1 урок по подорожавшей цене
    assert row.advance == Decimal('1800.00')


def test_refund_is_shown_and_row_reconciles(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-07-01')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10',
    )
    _add_payment(
        graph_cleanup, student_fixture, direction_fixture, 1, 1500, '2026-07-20', kind='refund',
    )

    row = _row(collect_monthly_report('2026-07'), pid)

    assert row.revenue_total == Decimal('500.00')
    assert row.refunded == Decimal('1500.00')
    assert row.advance == Decimal('0.00')


def test_every_row_reconciles(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Инвариант отчёта: Сумма + Доплаты = Выручка итого + Возвращено + Аванс."""
    _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 3, 1000, '2026-06-01')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10',
    )
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-11', duration=45,
    )

    for row in collect_monthly_report('2026-07').rows:
        assert row.total_amount + row.surcharge_amount == (
            row.revenue_total + row.refunded + row.advance
        ), f'строка не сходится: платёж {row.payment_id}'


def test_advance_matches_fifo_remaining_within_a_kopeck(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Аванс выводится из суммы строки — он обязан совпадать с FIFO-остатком."""
    from apps.finances.fifo import compute_fifo
    from apps.finances.repository import fifo_inputs

    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 3, 1000, '2026-06-01')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10',
    )

    row = _row(collect_monthly_report('2026-07'), pid)
    inp = fifo_inputs()
    key = str(student_fixture)
    cons = [c for c in inp['cons_by_key'].get(key, []) if c['date'] <= '2026-07-31']
    fifo = compute_fifo(inp['lots_by_key'][key], cons, '2026-07-01', '2026-08-01')

    assert abs(row.advance - fifo['remaining_by_payment'][pid]) <= Decimal('0.01')


def test_half_lesson_recognizes_half_the_price(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-07-01')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10', duration=45,
    )

    row = _row(collect_monthly_report('2026-07'), pid)

    assert row.revenue_by_month == {'2026-07': Decimal('250.00')}
    assert row.advance == Decimal('1750.00')


def test_free_lesson_recognizes_nothing(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Бесплатное занятие денег не берёт: выручки нет, вся сумма остаётся авансом."""
    pid = _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-07-01')
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10', is_free=True,
    )

    # Строка есть — деньги пришли в июле; признания по ней нет.
    row = _row(collect_monthly_report('2026-07'), pid)
    assert row.revenue_by_month == {}
    assert row.revenue_total == Decimal('0.00')
    assert row.advance == Decimal('2000')


def test_extra_payment_gets_its_own_row(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Доплата за доп.урок сверх курса — самостоятельная партия, значит и строка."""
    _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 1, 500, '2026-06-01')
    extra_pid = _add_payment(
        graph_cleanup, student_fixture, direction_fixture, 1, 1872, '2026-07-01', kind='extra',
    )
    # Первый урок гасит партию-предоплату, второй уходит уже в партию extra.
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-05',
    )
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-06',
    )

    row = _row(collect_monthly_report('2026-07'), extra_pid)

    assert row is not None
    # Направление — свойство самой оплаты (в FIFO партия extra лимит курса не
    # занимает и идёт без направления, но в реестре показываем то, что в оплате).
    assert row.direction_name == '__fin_dir__'
    assert row.revenue_by_month == {'2026-07': Decimal('468.00')}   # 1872 / 4


def test_rows_sorted_by_student_then_payment_date(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 1, 500, '2026-05-01')
    _add_payment_exact(graph_cleanup, student_fixture, direction_fixture, 1, 500, '2026-04-01')
    for date in ('2026-07-05', '2026-07-06'):
        _add_lesson_attendance(
            graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, date,
        )

    rows = [r for r in collect_monthly_report('2026-07').rows if r.student_id == student_fixture]

    assert [r.paid_at for r in rows] == ['2026-04-01', '2026-05-01']


def test_empty_report_still_reports_the_selected_month():
    report = collect_monthly_report('2019-01')
    assert report.rows == []
    assert report.months == ['2019-01']


def test_invalid_month_raises_value_error():
    with pytest.raises(ValueError):
        collect_monthly_report('2026-13')
