"""Смоук новой пер-ученик модели AbsenceResolution."""
from __future__ import annotations

import pytest
from django.db import connection, IntegrityError, transaction

from apps.extra_lessons.models import AbsenceResolution, MAKEUP_SCHEDULED

pytestmark = pytest.mark.django_db


def test_unique_missed_lesson_student(teacher_fixture, missed_lesson_fixture, student_fixture):
    # missed_lesson_fixture пишет урок через record_lesson → авто-создаёт pending
    # для student_fixture. Убираем его, чтобы проверить именно UNIQUE на паре
    # двух явных вставок ниже.
    with connection.cursor() as cur:
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id=%s AND student_id=%s',
                    [missed_lesson_fixture, student_fixture])
    AbsenceResolution.objects.create(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture,
        assigned_teacher_id=teacher_fixture, status=MAKEUP_SCHEDULED,
        scheduled_date='2026-04-05', scheduled_time='15:00', duration_minutes=45)
    try:
        with transaction.atomic(), pytest.raises(IntegrityError):
            AbsenceResolution.objects.create(
                missed_lesson_id=missed_lesson_fixture, student_id=student_fixture,
                assigned_teacher_id=teacher_fixture, status=MAKEUP_SCHEDULED,
                scheduled_date='2026-04-06', scheduled_time='16:00', duration_minutes=45)
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [missed_lesson_fixture])
