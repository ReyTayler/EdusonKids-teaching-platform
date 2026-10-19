"""
Тесты «Бухгалтерского отчёта» по ученикам за месяц
(apps/finances/student_month.py::collect_student_month).

Строка = ученик × оплата: своя строка на каждую оплату, деньги которой
отрабатывались в месяце, и на каждую оплату месяца. Уроки сверх оплаченных —
отдельная строка-долг. Фикстуры — apps/finances/tests/conftest.py; БД общая
(managed=False), поэтому свои строки ищем по student_id.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.finances.student_month import collect_student_month

pytestmark = pytest.mark.django_db


def _add_payment(created, student_id, direction_id, lessons_count, unit_price, paid_at,
                 kind='purchase'):
    total = lessons_count * unit_price
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,1,%s,%s,%s,%s,%s,'test') RETURNING id",
            [student_id, direction_id, lessons_count, kind, unit_price, total, paid_at],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


def _add_lesson(created, group_id, teacher_id, student_id, date,
                duration=60, is_free=False, present=True):
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
            'VALUES (%s,%s,%s,%s)',
            [lid, student_id, present, is_free],
        )
    created['lessons'].append(lid)
    return lid


def _add_refund(created, student_id, direction_id, lessons, amount, paid_at):
    """Строка возврата: уроки и сумма со знаком минус (CHECK payments_refund_signs)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, lessons_count, kind, "
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,%s,'refund',0,%s,%s,'test') RETURNING id",
            [student_id, direction_id, -lessons, -amount, paid_at],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


def _rows(month, student_id):
    return [r for r in collect_student_month(month) if r.student_id == student_id]


def test_lessons_paid_from_two_payments_give_two_rows(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """
    Часть уроков месяца отработана давней оплатой, часть — оплатой месяца:
    две строки с разной стоимостью урока.
    """
    with connection.cursor() as cur:
        cur.execute("UPDATE students SET platform_id = 'PL-7' WHERE id = %s", [student_fixture])
    old_pid = _add_payment(graph_cleanup, student_fixture, direction_fixture, 2, 500, '2026-06-01')
    new_pid = _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 600, '2026-07-05')
    # 3 урока в июле: 2 добивают июньскую оплату, третий идёт из июльской.
    for date in ('2026-07-10', '2026-07-11', '2026-07-12'):
        _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, date)

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 2
    old_row, new_row = rows
    assert old_row.payment_id == old_pid
    assert old_row.platform_id == 'PL-7'
    assert old_row.attended_lessons == 2
    assert old_row.worked_off == Decimal('1000.00')
    assert old_row.unit_price == Decimal('500.00')
    assert old_row.paid_at == '2026-06-01'
    assert old_row.paid_in_month == Decimal('0')      # оплата не этого месяца
    assert old_row.remaining_lessons == 0
    assert old_row.remaining_value == Decimal('0.00')

    assert new_row.payment_id == new_pid
    assert new_row.attended_lessons == 1
    assert new_row.worked_off == Decimal('600.00')
    assert new_row.unit_price == Decimal('600.00')
    assert new_row.paid_in_month == Decimal('2400.00')
    assert new_row.remaining_lessons == 3
    assert new_row.remaining_value == Decimal('1800.00')


def test_old_payment_not_fully_worked_off_still_shows_advance(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Оплата не этого месяца: «Итого оплачено» = 0, но аванс показываем."""
    pid = _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-06-01')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10')

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 1
    assert rows[0].payment_id == pid
    assert rows[0].paid_in_month == Decimal('0')
    assert rows[0].worked_off == Decimal('500.00')
    assert rows[0].remaining_lessons == 3
    assert rows[0].remaining_value == Decimal('1500.00')


def test_payment_of_month_without_lessons_shows_price_and_advance(
    student_fixture, direction_fixture, graph_cleanup,
):
    """Оплата месяца без уроков: 0 посещений, но цена урока и аванс на месте."""
    pid = _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-07-05')

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 1
    row = rows[0]
    assert row.payment_id == pid
    assert row.attended_lessons == 0
    assert row.worked_off == Decimal('0.00')
    assert row.unit_price == Decimal('500.00')
    assert row.paid_in_month == Decimal('2000.00')
    assert row.remaining_lessons == 4
    assert row.remaining_value == Decimal('2000.00')


def test_lessons_beyond_paid_give_a_debt_row(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Уроки сверх оплаченных не привязаны к оплате — им отдельная строка-долг."""
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 2, 500, '2026-06-01')
    for date in ('2026-07-05', '2026-07-06', '2026-07-07', '2026-07-08'):
        _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, date)

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 2
    paid_row, debt_row = rows
    assert paid_row.attended_lessons == 2
    assert paid_row.worked_off == Decimal('1000.00')
    assert paid_row.debt == Decimal('0.00')

    assert debt_row.payment_id is None
    assert debt_row.attended_lessons == 2              # 2 урока сверх оплаченных
    assert debt_row.worked_off == Decimal('0.00')
    assert debt_row.unit_price == Decimal('500.00')    # по цене последней оплаты
    assert debt_row.debt == Decimal('1000.00')
    assert debt_row.paid_in_month == Decimal('0')
    assert debt_row.remaining_lessons == -2           # баланс ученика в минусе


def test_debt_carried_from_previous_month_stays_visible(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Долг — величина на конец месяца: перешедший с июня виден и в июле."""
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 500, '2026-06-01')
    for date in ('2026-06-05', '2026-06-06'):
        _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, date)

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 1
    debt_row = rows[0]
    assert debt_row.payment_id is None
    assert debt_row.attended_lessons == 0             # в июле в минус не уходил
    assert debt_row.debt == Decimal('500.00')         # но долг с июня остался


def test_half_lesson_counts_as_half(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-06-01')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10',
                duration=45)

    row = _rows('2026-07', student_fixture)[0]

    assert row.attended_lessons == 0.5
    assert row.worked_off == Decimal('250.00')
    assert row.remaining_lessons == 3.5


def test_free_lesson_debits_nothing(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Бесплатное занятие баланс не списывает: посещений 0, оплата не тронута."""
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-06-01')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-12',
                is_free=True)

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 1
    assert rows[0].attended_lessons == 0
    assert rows[0].worked_off == Decimal('0.00')
    assert rows[0].remaining_lessons == 4   # все 4 урока целы


def test_as_of_end_of_month(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Уроки и оплаты после конца месяца на отчёт не влияют."""
    pid = _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-07-01')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-10')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-08-05')
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-08-01')

    rows = _rows('2026-07', student_fixture)

    assert [r.payment_id for r in rows] == [pid]       # августовской оплаты нет
    assert rows[0].attended_lessons == 1
    assert rows[0].remaining_lessons == 3              # августовский урок не списан
    assert rows[0].remaining_value == Decimal('1500.00')


def test_student_without_activity_gets_one_empty_row(student_fixture):
    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 1
    row = rows[0]
    assert row.payment_id is None
    assert row.attended_lessons == 0
    assert row.worked_off == Decimal('0')
    assert row.unit_price is None
    assert row.paid_at is None
    assert row.paid_in_month == Decimal('0')
    assert row.remaining_lessons == 0
    assert row.remaining_value == Decimal('0')
    assert row.debt == Decimal('0')


def test_rows_grouped_by_student_in_name_order(student_fixture):
    rows = collect_student_month('2026-07')
    names = [r.full_name for r in rows]

    assert names == sorted(names)                       # ученики по алфавиту
    assert student_fixture in {r.student_id for r in rows}


def test_invalid_month_raises_value_error():
    with pytest.raises(ValueError):
        collect_student_month('2026-13')

def test_untouched_payment_still_shows_its_balance(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """
    Снимок на конец месяца: остаток виден у КАЖДОЙ живой оплаты, даже если в
    месяце по ней не было ни движения, ни денег.
    """
    pid = _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-05-05')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-05-10')

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 1
    row = rows[0]
    assert row.payment_id == pid
    assert row.attended_lessons == 0          # в июле не занимался
    assert row.worked_off == Decimal('0.00')
    assert row.paid_in_month == Decimal('0')  # и не платил
    assert row.remaining_lessons == 3         # но 3 урока на 31.07 у него есть
    assert row.remaining_value == Decimal('1500.00')


def test_payment_made_after_month_end_does_not_exist_in_snapshot(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """
    Урок конца месяца, оплаченный уже в следующем месяце, на снимке остаётся
    долгом: на 31.07 этих денег ещё не было.
    """
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-08-03')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-07-28')

    rows = _rows('2026-07', student_fixture)

    assert len(rows) == 1
    debt_row = rows[0]
    assert debt_row.payment_id is None            # августовской оплаты в июле нет
    assert debt_row.attended_lessons == 1
    assert debt_row.worked_off == Decimal('0.00')
    assert debt_row.remaining_lessons == -1
    # Цены на отчётную дату ещё не существовало — берём тариф ближайшей оплаты.
    assert debt_row.debt == Decimal('500.00')



def test_refund_of_month_is_shown_on_its_payment_row(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Возврат месяца — на строке оплаты, из которой вернули деньги. Строка есть,
    даже если после возврата остаток нулевой и уроков в месяце не было: иначе
    сверка аванса за месяц теряет сумму возврата."""
    pid = _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-06-01')
    _add_lesson(graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-10')
    _add_refund(graph_cleanup, student_fixture, direction_fixture, 3, 1500, '2026-07-15')

    june = _rows('2026-06', student_fixture)
    july = _rows('2026-07', student_fixture)

    assert [(r.payment_id, r.refunded_in_month, r.remaining_value) for r in june] == [
        (pid, Decimal('0'), Decimal('1500.00'))]
    assert [(r.payment_id, r.refunded_in_month, r.remaining_value) for r in july] == [
        (pid, Decimal('1500.00'), Decimal('0.00'))]
    # Сверка бухгалтерии: аванс июля = аванс июня − отработано + оплачено − возврат.
    advance = lambda rows: sum((r.remaining_value for r in rows), Decimal('0'))
    total = lambda rows, attr: sum((getattr(r, attr) for r in rows), Decimal('0'))
    assert advance(july) == (advance(june) - total(july, 'worked_off')
                             + total(july, 'paid_in_month') - total(july, 'refunded_in_month'))


def test_refund_after_month_end_is_not_in_snapshot(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup,
):
    """Возврат следующего месяца в снимок не входит: аванс на конец месяца целый."""
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 4, 500, '2026-06-01')
    _add_refund(graph_cleanup, student_fixture, direction_fixture, 4, 2000, '2026-08-03')

    rows = _rows('2026-07', student_fixture)

    assert [(r.refunded_in_month, r.remaining_value) for r in rows] == [
        (Decimal('0'), Decimal('2000.00'))]
