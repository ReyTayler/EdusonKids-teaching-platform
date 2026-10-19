"""
Прямой тест attended_units_total — ЕДИНОГО источника «отработано»
(present=true по ВСЕМ подтипам урока, half-lesson 45мин=0.5). И баланс finances,
и движок продлений считают потребление через эту функцию, поэтому она покрыта
напрямую. Модель 1c: доп.урок (lesson_type='extra', present=true) считается как
любой урок (раньше исключался). См.
docs/superpowers/plans/2026-07-18-unify-absences-phase-1c.md.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.finances.repository import attended_units_total

pytestmark = pytest.mark.django_db


def _add_lesson(gid, tid, sid, graph, *, present, lesson_type='regular', duration=60, number=1):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,'2026-06-01',%s,%s,%s,'__au_test__') RETURNING id",
            [gid, tid, number, duration, lesson_type])
        lid = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,%s)',
            [lid, sid, present])
    graph['lessons'].append(lid)
    return lid


def test_attended_units_total_counts_all_present_subtypes_and_half_lesson(
    student_fixture, group_fixture, teacher_id_fixture, graph_cleanup,
):
    """1 обычный (60мин, present)=1 + 1 половинный (45мин, present)=0.5 + 1 доп.урок
    (extra, 60мин, present)=1 + 1 пропуск (present=false)=0 → 2.5. Модель 1c: доп.урок
    present=true считается в потребление, как любой урок (исходный пропуск — false)."""
    sid, gid, tid = student_fixture, group_fixture, teacher_id_fixture
    _add_lesson(gid, tid, sid, graph_cleanup, present=True, duration=60, number=1)
    _add_lesson(gid, tid, sid, graph_cleanup, present=True, duration=45, number=2)
    _add_lesson(gid, tid, sid, graph_cleanup, present=True, lesson_type='extra', duration=60, number=3)
    _add_lesson(gid, tid, sid, graph_cleanup, present=False, duration=60, number=4)
    assert attended_units_total(sid) == Decimal('2.5')


def test_attended_units_total_zero_without_attendance(student_fixture):
    """Ученик без единой посещённой строки → Decimal('0') (Coalesce, не None)."""
    assert attended_units_total(student_fixture) == Decimal('0')
