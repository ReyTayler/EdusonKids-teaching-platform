"""Второй рубеж против двойного учёта: burn()/record() makeup-заявки работают
только если на пропущенном уроке ученик ДЕЙСТВИТЕЛЬНО отсутствовал. Состояние
«заявка жива, а ученик отмечен был» воспроизводим прямым UPDATE ячейки — так
оно выглядело на проде до фикса отметки (группа ВДГ18, урок №32, 2026-09-19)."""
from __future__ import annotations

import pytest
from django.db import connection

from apps.extra_lessons import services
from apps.extra_lessons.exceptions import StudentNotAbsent
from apps.extra_lessons.models import BURNED, MAKEUP_SCHEDULED, PENDING, AbsenceResolution
from apps.lessons.models import Lesson

pytestmark = pytest.mark.django_db


class _FakeRequest:
    META: dict = {}
    user = None


def _mark_present(lesson_id, student_id):
    with connection.cursor() as cur:
        cur.execute('UPDATE lesson_attendance SET present=true '
                    'WHERE lesson_id=%s AND student_id=%s', [lesson_id, student_id])


@pytest.fixture
def pending_resolution(missed_lesson_fixture, student_fixture):
    """pending-резолюция, авто-созданная missed_lesson_fixture. Teardown сносит
    возможный факт ДО teardown missed_lesson_fixture (DB-level FK) — общая
    eduson_test не должна копить мусор, если тест упал на полпути."""
    res = AbsenceResolution.objects.get(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture, status=PENDING)
    yield res
    tokens = [f'burn:{res.id}', f'makeup:{res.id}']
    with connection.cursor() as cur:
        cur.execute('UPDATE absence_resolutions SET fact_lesson_id=NULL WHERE id=%s', [res.id])
        cur.execute('SELECT id FROM lessons WHERE submitted_by_token = ANY(%s)', [tokens])
        fact_ids = [r[0] for r in cur.fetchall()]
        for fid in fact_ids:
            cur.execute('DELETE FROM payroll WHERE lesson_id=%s', [fid])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id=%s', [fid])
            cur.execute('DELETE FROM lessons WHERE id=%s', [fid])


def test_burn_refused_when_student_was_present(pending_resolution):
    _mark_present(pending_resolution.missed_lesson_id, pending_resolution.student_id)
    with pytest.raises(StudentNotAbsent):
        services.burn(pending_resolution.id, request=_FakeRequest(), burn_date='2026-07-18')
    pending_resolution.refresh_from_db()
    assert pending_resolution.status == PENDING
    assert pending_resolution.fact_lesson_id is None
    assert not Lesson.objects.filter(
        submitted_by_token=f'burn:{pending_resolution.id}').exists()


def test_record_refused_when_student_was_present(pending_resolution, teacher_fixture):
    with connection.cursor() as cur:
        cur.execute(
            'UPDATE absence_resolutions SET status=%s, assigned_teacher_id=%s, '
            "scheduled_date='2026-04-05', duration_minutes=60 WHERE id=%s",
            [MAKEUP_SCHEDULED, teacher_fixture, pending_resolution.id])
    _mark_present(pending_resolution.missed_lesson_id, pending_resolution.student_id)
    with pytest.raises(StudentNotAbsent):
        services.record(
            pending_resolution.id, teacher_id=teacher_fixture, present=True,
            record_url=None, submitted_by_token='t', submit_date='2026-04-05',
            request=_FakeRequest())
    pending_resolution.refresh_from_db()
    assert pending_resolution.status == MAKEUP_SCHEDULED
    assert not Lesson.objects.filter(
        submitted_by_token=f'makeup:{pending_resolution.id}').exists()


def test_burn_still_works_for_real_absence(pending_resolution):
    """Контроль: реальный пропуск по-прежнему сжигается."""
    services.burn(pending_resolution.id, request=_FakeRequest(), burn_date='2026-07-18')
    pending_resolution.refresh_from_db()
    assert pending_resolution.status == BURNED
    services.delete_fact(pending_resolution.id, _FakeRequest())
