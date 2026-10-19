"""
Тесты student_lesson_recognition — сколько денег списал каждый урок ученика.

Источник колонки «Признано» во вкладке «Уроки» карточки ученика
(спека 2026-09-14). Считается точным FIFO по всей истории ученика.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.finances.repository import student_lesson_recognition

pytestmark = pytest.mark.django_db


def _add_payment(sid, did, lessons, total, graph_cleanup, kind='purchase', subs=1,
                 paid_at='2026-01-01'):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
            "lessons_count, kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'t') RETURNING id",
            [sid, did, subs, lessons, kind, 0, total, paid_at])
        pid = cur.fetchone()[0]
    graph_cleanup['payments'].append(pid)
    return pid


def _add_lesson(gid, tid, sid, graph_cleanup, *, date='2026-02-10', number=1,
                duration=60, lesson_type='regular', present=True, is_free=False):
    """Урок + запись посещаемости ученика. Возвращает lesson_id."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, %s, %s, %s, %s, 't') RETURNING id",
            [gid, tid, date, number, duration, lesson_type])
        lid = cur.fetchone()[0]
        graph_cleanup['lessons'].append(lid)
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
            'VALUES (%s, %s, %s, %s)', [lid, sid, present, is_free])
    return lid


def test_regular_lesson_recognizes_lot_price(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    lid = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup)

    rec = student_lesson_recognition(student_fixture)
    assert rec[lid] == {'recognized': Decimal('1000.00'), 'is_debt': False}


def test_half_lesson_recognizes_half_price(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """45 минут = 0.5 урока — половина цены партии."""
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    lid = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                      duration=45)

    rec = student_lesson_recognition(student_fixture)
    assert rec[lid] == {'recognized': Decimal('500.00'), 'is_debt': False}


def test_free_lesson_recognizes_nothing(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """За бесплатное занятие деньги не берутся — его в карте нет вовсе.

    Платный урок рядом — контроль: его присутствие доказывает, что бесплатный
    пропал именно из-за is_free, а не потому что карта вышла пустой.
    """
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    paid = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                       date='2026-02-10', number=1)
    free = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                       date='2026-02-11', number=2, is_free=True)

    rec = student_lesson_recognition(student_fixture)
    assert rec[paid] == {'recognized': Decimal('1000.00'), 'is_debt': False}
    assert free not in rec


def test_extra_and_burned_lessons_consume_money(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """Доп.урок и сгорание — такие же списания с партий, как обычный урок."""
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    extra = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                        date='2026-02-11', number=2, lesson_type='extra')
    burned = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                         date='2026-02-12', number=3, lesson_type='burned')

    rec = student_lesson_recognition(student_fixture)
    assert rec[extra] == {'recognized': Decimal('1000.00'), 'is_debt': False}
    assert rec[burned] == {'recognized': Decimal('1000.00'), 'is_debt': False}


def test_lesson_beyond_paid_balance_is_debt(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """Оплачен 1 урок, проведено 2 — второй в долг: 0 ₽ и флаг."""
    _add_payment(student_fixture, direction_fixture, 1, 1000, graph_cleanup)
    first = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                        date='2026-02-10', number=1)
    second = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                         date='2026-02-11', number=2)

    rec = student_lesson_recognition(student_fixture)
    assert rec[first] == {'recognized': Decimal('1000.00'), 'is_debt': False}
    assert rec[second] == {'recognized': Decimal('0.00'), 'is_debt': True}


def test_partially_paid_lesson_is_both_recognized_and_debt(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """Урок, оплаченный наполовину: деньги признаны И флаг долга стоит.

    is_debt — не «денег ноль», а «часть урока прошла сверх оплаченного».
    Оплачен 1 урок за 500; 45-минутный съедает 0.5, второму (целому) достаётся
    только оставшаяся половина партии.
    """
    _add_payment(student_fixture, direction_fixture, 1, 500, graph_cleanup)
    half = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                       date='2026-02-10', number=1, duration=45)
    partial = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                          date='2026-02-11', number=2)

    rec = student_lesson_recognition(student_fixture)
    assert rec[half] == {'recognized': Decimal('250.00'), 'is_debt': False}
    assert rec[partial] == {'recognized': Decimal('250.00'), 'is_debt': True}


def test_lesson_spanning_two_payments_sums_both_prices(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """Половинный урок на стыке абонементов гасит обе партии — сумма долей."""
    _add_payment(student_fixture, direction_fixture, 1, 1000, graph_cleanup,
                 paid_at='2026-01-01')
    _add_payment(student_fixture, direction_fixture, 4, 2000, graph_cleanup,
                 paid_at='2026-01-15')
    first = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                        date='2026-02-10', number=1, duration=45)
    second = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                         date='2026-02-11', number=2, duration=60)

    rec = student_lesson_recognition(student_fixture)
    # Первая партия: 1 урок по 1000. Половинный урок съел 0.5 → 500.
    assert rec[first] == {'recognized': Decimal('500.00'), 'is_debt': False}
    # Второй урок: остаток первой партии 0.5 × 1000 + 0.5 × 500 = 750.
    assert rec[second] == {'recognized': Decimal('750.00'), 'is_debt': False}


def test_lesson_after_refund_becomes_debt(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """Возврат гасит остаток — урок после него проведён в долг."""
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup,
                 paid_at='2026-01-01')
    _add_payment(student_fixture, None, -4, -4000, graph_cleanup, kind='refund',
                 subs=None, paid_at='2026-01-05')
    lid = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                      date='2026-02-10', number=1)

    rec = student_lesson_recognition(student_fixture)
    assert rec[lid] == {'recognized': Decimal('0.00'), 'is_debt': True}


def test_refund_after_lessons_keeps_their_recognition(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """Возврат гасит только ОСТАТОК — уже признанную выручку он не переписывает.

    Оплачено 4 урока, проведено 2, возвращены оставшиеся 2: оба проведённых
    урока остаются признанными по цене партии и в долг не уходят.
    """
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup,
                 paid_at='2026-01-01')
    first = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                        date='2026-02-10', number=1)
    second = _add_lesson(group_fixture, teacher_id_fixture, student_fixture, graph_cleanup,
                         date='2026-02-11', number=2)
    _add_payment(student_fixture, None, -2, -2000, graph_cleanup, kind='refund',
                 subs=None, paid_at='2026-03-01')

    rec = student_lesson_recognition(student_fixture)
    assert rec[first] == {'recognized': Decimal('1000.00'), 'is_debt': False}
    assert rec[second] == {'recognized': Decimal('1000.00'), 'is_debt': False}


def test_no_payments_no_lessons_gives_empty_map(student_fixture):
    assert student_lesson_recognition(student_fixture) == {}
