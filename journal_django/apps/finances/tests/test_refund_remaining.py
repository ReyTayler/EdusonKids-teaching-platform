"""Тесты student_fifo_remaining — неотработанный остаток ученика."""
from __future__ import annotations

import pytest
from decimal import Decimal
from django.db import connection

from apps.finances.repository import student_fifo_remaining

pytestmark = pytest.mark.django_db


def _add_payment(sid, did, lessons, total, graph_cleanup, kind='purchase', subs=1):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
            "lessons_count, kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'2026-01-01','t') RETURNING id",
            [sid, did, subs, lessons, kind, 0, total])
        pid = cur.fetchone()[0]
    graph_cleanup['payments'].append(pid)
    return pid


def test_remaining_no_attendance(student_fixture, direction_fixture, graph_cleanup):
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    r = student_fifo_remaining(student_fixture)
    assert r['remaining_lessons'] == 4
    assert r['remaining_value'] == Decimal('4000.00')


def test_remaining_zero_when_no_payments(student_fixture):
    r = student_fifo_remaining(student_fixture)
    assert r['remaining_lessons'] == 0
    assert r['remaining_value'] == Decimal('0.00')


# ---------------------------------------------------------------------------
# remaining_by_direction — на чьи направления записывать возврат
# ---------------------------------------------------------------------------

def test_remaining_by_direction_attributes_tail_to_payment_direction(
        student_fixture, direction_fixture, graph_cleanup):
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    r = student_fifo_remaining(student_fixture)
    assert r['remaining_by_direction'] == {
        direction_fixture: {'lessons': Decimal('4'), 'value': Decimal('4000.00')},
    }


def test_remaining_by_direction_extra_topup_has_no_direction(
        student_fixture, direction_fixture, graph_cleanup):
    """Доплата сверх курса (kind='extra') лимитом направления не считается —
    и её возврат не должен освобождать лимит. Поэтому в разбивке она без направления."""
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    _add_payment(student_fixture, direction_fixture, 1, 800, graph_cleanup, kind='extra')
    r = student_fifo_remaining(student_fixture)
    assert r['remaining_by_direction'] == {
        direction_fixture: {'lessons': Decimal('4'), 'value': Decimal('4000.00')},
        None: {'lessons': Decimal('1'), 'value': Decimal('800.00')},
    }


def test_free_lesson_does_not_eat_remaining_value(
        student_fixture, direction_fixture, group_fixture, teacher_id_fixture, graph_cleanup):
    """За бесплатное занятие деньги не берутся: остаток к возврату не уменьшается.

    Тот же инвариант, что в balances_for_students и fifo_inputs (там is_free=False).
    Иначе возврат вернул бы клиенту меньше, чем показывает баланс.
    """
    from apps.finances.repository import balance_for_student
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-02-10', 1, 60, 'group', 't') RETURNING id",
            [group_fixture, teacher_id_fixture])
        lesson_id = cur.fetchone()[0]
        graph_cleanup['lessons'].append(lesson_id)
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
            'VALUES (%s, %s, true, true)', [lesson_id, student_fixture])

    r = student_fifo_remaining(student_fixture)
    assert balance_for_student(student_fixture) == 4
    assert r['remaining_lessons'] == 4
    assert r['remaining_value'] == Decimal('4000.00')


def test_remaining_by_direction_empty_after_refund(
        student_fixture, direction_fixture, graph_cleanup):
    _add_payment(student_fixture, direction_fixture, 4, 4000, graph_cleanup)
    _add_payment(student_fixture, None, -4, -4000, graph_cleanup, kind='refund', subs=None)
    r = student_fifo_remaining(student_fixture)
    assert r['remaining_value'] == Decimal('0.00')
    assert r['remaining_by_direction'] == {}
