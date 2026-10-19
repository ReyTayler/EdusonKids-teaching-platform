"""
Фаза 1b — сквозной жизненный цикл резолюции пропуска + обратимость денег.

Записанный обычный урок с отсутствующим учеником авто-создаёт pending; назначение
→ makeup_scheduled; проведение → makeup_done (списывается 1 урок, преподавателю
200₽); откат факта → pending (деньги и баланс возвращаются к исходному состоянию).
Ключевое: после полного цикла + отката числа бит-в-бит те же, что до цикла.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.extra_lessons import services
from apps.finances import repository as fin_repo

pytestmark = pytest.mark.django_db


class _FakeRequest:
    META = {}
    user = None


def _status(missed_lesson_id, student_id):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT status FROM absence_resolutions WHERE missed_lesson_id=%s AND student_id=%s',
            [missed_lesson_id, student_id])
        row = cur.fetchone()
    return row[0] if row else None


def _payroll_sum(teacher_id):
    with connection.cursor() as cur:
        cur.execute('SELECT COALESCE(SUM(payment),0) FROM payroll WHERE teacher_id=%s', [teacher_id])
        return int(cur.fetchone()[0])


def test_full_lifecycle_and_money_reversibility(
    group_fixture, teacher_fixture, student_fixture, membership_fixture, missed_lesson_fixture,
):
    # --- БАЗА: урок записан (present=false), авто-создан pending, деньги не тронуты ---
    assert _status(missed_lesson_fixture, student_fixture) == 'pending'
    base_balance = fin_repo.balance_for_student(student_fixture)
    assert base_balance == 8
    assert fin_repo.attended_units_total(student_fixture) == Decimal('0')

    # --- Назначение: pending → makeup_scheduled ---
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest())
    rid = created['resolution_ids'][0]
    assert _status(missed_lesson_fixture, student_fixture) == 'makeup_scheduled'
    # Назначение денег не двигает.
    assert fin_repo.balance_for_student(student_fixture) == 8

    # --- Проведение: makeup_scheduled → makeup_done, списывается 1 урок, 200₽ ---
    result = services.record(
        rid, teacher_id=teacher_fixture, present=True, record_url=None,
        submitted_by_token='acct:1', submit_date='2026-04-05', request=_FakeRequest())
    assert result['payment'] == 200
    assert _status(missed_lesson_fixture, student_fixture) == 'makeup_done'
    assert fin_repo.attended_units_total(student_fixture) == Decimal('1')
    assert fin_repo.balance_for_student(student_fixture) == 7
    assert _payroll_sum(teacher_fixture) == 200

    # --- Откат факта: makeup_done → pending, деньги возвращаются к базе ---
    ok = services.delete_fact(rid, _FakeRequest())
    assert ok is True
    assert _status(missed_lesson_fixture, student_fixture) == 'pending'
    assert fin_repo.attended_units_total(student_fixture) == Decimal('0')
    assert fin_repo.balance_for_student(student_fixture) == base_balance  # 8, бит-в-бит
    assert _payroll_sum(teacher_fixture) == 0
