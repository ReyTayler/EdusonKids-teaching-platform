"""Сверка lifecycle «сжечь → откат» (Фаза 1c-2): balance / attended /
renewals-прогресс / payroll реверсируются точно; исходный пропуск present=false
всё время; burned-факт потребляется штатно (attendance.burned_at IS NULL — новый
путь не использует старый burn-WIP-приоритет даты)."""
from __future__ import annotations

import pytest
from django.db import connection

from apps.extra_lessons import services
from apps.extra_lessons.models import PENDING, AbsenceResolution
from apps.finances import repository as fin_repo
from apps.lessons.models import Lesson, LessonAttendance
from apps.payroll.models import Payroll
from apps.renewals import engine as renewals_engine

pytestmark = pytest.mark.django_db


class _FakeRequest:
    META: dict = {}
    user = None


@pytest.fixture
def absence_pending(missed_lesson_fixture, student_fixture):
    """pending-резолюция (авто-создана missed_lesson_fixture). Teardown сносит
    burned-факт + резолюции ДО teardown missed_lesson_fixture (DB-level FK)."""
    res = AbsenceResolution.objects.get(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture, status=PENDING)
    yield res
    with connection.cursor() as cur:
        cur.execute(
            'SELECT fact_lesson_id FROM absence_resolutions WHERE missed_lesson_id = %s '
            'AND fact_lesson_id IS NOT NULL', [missed_lesson_fixture])
        fact_ids = [r[0] for r in cur.fetchall()]
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [missed_lesson_fixture])
        for fid in fact_ids:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [fid])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [fid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [fid])


def test_burn_lifecycle_reconciles(absence_pending):
    student_id = absence_pending.student_id
    missed = Lesson.objects.get(id=absence_pending.missed_lesson_id)

    bal0 = fin_repo.balance_for_student(student_id)
    att0 = fin_repo.attended_units_total(student_id)
    ren0 = renewals_engine._attended_total(student_id)

    services.burn(absence_pending.id, request=_FakeRequest(), burn_date='2026-07-18')
    absence_pending.refresh_from_db()
    fact_id = absence_pending.fact_lesson_id

    # Списание ровно на 1 (60-мин пропуск), payroll 200 в месяц сжигания,
    # прогресс продлений +1, исходный пропуск остаётся present=false.
    assert fin_repo.balance_for_student(student_id) == bal0 - 1
    assert fin_repo.attended_units_total(student_id) == att0 + 1
    assert renewals_engine._attended_total(student_id) == ren0 + 1.0
    assert Payroll.objects.get(lesson_id=fact_id).payment == 200
    assert Lesson.objects.get(id=fact_id).lesson_date.isoformat() == '2026-07-18'
    assert LessonAttendance.objects.get(
        lesson_id=missed.id, student_id=student_id).present is False

    # Откат — числа возвращаются точно.
    services.delete_fact(absence_pending.id, _FakeRequest())
    absence_pending.refresh_from_db()
    assert absence_pending.status == PENDING
    assert fin_repo.balance_for_student(student_id) == bal0
    assert fin_repo.attended_units_total(student_id) == att0
    assert renewals_engine._attended_total(student_id) == ren0
    assert not Payroll.objects.filter(lesson_id=fact_id).exists()
    assert LessonAttendance.objects.get(
        lesson_id=missed.id, student_id=student_id).present is False


def test_burned_fact_consumes_in_its_own_month(absence_pending):
    """Burned-факт потребляется в свою дату (lesson_date=дата сжигания)."""
    services.burn(absence_pending.id, request=_FakeRequest(), burn_date='2026-07-18')
    absence_pending.refresh_from_db()
    fact = Lesson.objects.get(id=absence_pending.fact_lesson_id)
    assert fact.lesson_date.isoformat() == '2026-07-18'
