"""Миграция 1c-1: исторический makeup_done → исходный present=false + длительность
extra-факта = длительности исходного урока; lessons_done не двигается; идемпотентно."""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.extra_lessons._migration_helpers import revert_historical_makeups

pytestmark = pytest.mark.django_db


def _lessons_done(group_id, student_id):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT lessons_done FROM group_memberships WHERE group_id=%s AND student_id=%s',
            [group_id, student_id])
        row = cur.fetchone()
    return row[0] if row else None


def test_revert_historical_makeup(
    group_fixture, teacher_fixture, student_fixture, membership_fixture, missed_lesson_fixture,
):
    """Синтетика «как до 1c»: исходный 60-мин урок present=true (apply_makeup),
    extra-факт 45-мин (длительность назначения) present=true, резолюция makeup_done,
    lessons_done += 1. После revert: исходный present=false, extra-факт 60-мин,
    lessons_done без изменений."""
    with connection.cursor() as cur:
        # Исходный урок: смоделировать historical apply_makeup — present=true + lessons_done +1.
        cur.execute(
            'UPDATE lesson_attendance SET present=true WHERE lesson_id=%s AND student_id=%s',
            [missed_lesson_fixture, student_fixture])
        cur.execute(
            'UPDATE group_memberships SET lessons_done = lessons_done + 1 '
            'WHERE group_id=%s AND student_id=%s', [group_fixture, student_fixture])
        # Extra-факт: 45-мин (историческая длительность назначения), present=true.
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,'2026-04-05',1,45,'extra','__mig1c__') RETURNING id",
            [group_fixture, teacher_fixture])
        fact_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,true)',
            [fact_id, student_fixture])
        # Резолюция: missed_lesson_fixture уже авто-создал pending (record_lesson) —
        # переводим ЕЁ в makeup_done со ссылкой на факт (UNIQUE per пропуск×ученик).
        cur.execute(
            "UPDATE absence_resolutions SET status='makeup_done', fact_lesson_id=%s "
            "WHERE missed_lesson_id=%s AND student_id=%s",
            [fact_id, missed_lesson_fixture, student_fixture])

    ld_before = _lessons_done(group_fixture, student_fixture)
    try:
        revert_historical_makeups(connection)

        with connection.cursor() as cur:
            cur.execute(
                'SELECT present FROM lesson_attendance WHERE lesson_id=%s AND student_id=%s',
                [missed_lesson_fixture, student_fixture])
            assert cur.fetchone()[0] is False  # исходный вернулся в present=false
            cur.execute('SELECT lesson_duration_minutes FROM lessons WHERE id=%s', [fact_id])
            assert cur.fetchone()[0] == 60  # extra-факт → длительность исходного урока
        assert _lessons_done(group_fixture, student_fixture) == ld_before  # не тронут

        # Идемпотентность: повторный вызов ничего не меняет.
        revert_historical_makeups(connection)
        with connection.cursor() as cur:
            cur.execute(
                'SELECT present FROM lesson_attendance WHERE lesson_id=%s AND student_id=%s',
                [missed_lesson_fixture, student_fixture])
            assert cur.fetchone()[0] is False
        assert _lessons_done(group_fixture, student_fixture) == ld_before
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id=%s', [missed_lesson_fixture])
            cur.execute('DELETE FROM payroll WHERE lesson_id=%s', [fact_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id=%s', [fact_id])
            cur.execute('DELETE FROM lessons WHERE id=%s', [fact_id])
