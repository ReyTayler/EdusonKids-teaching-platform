"""Тесты «Сжечь» (Фаза 1c-2): статус burned, repository.mark_burned, сервис
burn() и обобщённый delete_fact (откат сгорания). Реальная БД (eduson_test)."""
from __future__ import annotations

import pytest
from django.db import connection

from apps.extra_lessons import repository, services
from apps.extra_lessons.models import (
    BURNED, MAKEUP_DONE, PENDING, STATUS_CHOICES, AbsenceResolution,
)
from apps.lessons.models import Lesson, LessonAttendance
from apps.payroll.models import Payroll

pytestmark = pytest.mark.django_db


class _FakeRequest:
    """Минимальная заглушка request для log_event (без HTTP-контекста)."""
    META: dict = {}
    user = None


def _cleanup_fact(lesson_id):
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


@pytest.fixture
def absence_pending(missed_lesson_fixture, student_fixture):
    """pending-резолюция реально отсутствовавшего ученика.

    missed_lesson_fixture записан через create_lesson_full → record_lesson УЖЕ
    авто-создал pending (regular-урок, present=false). Берём существующую строку,
    НЕ создаём вторую (UNIQUE(missed_lesson, student)). Teardown сносит созданный
    burned-факт и все резолюции пропуска ДО teardown missed_lesson_fixture
    (absence_resolutions.missed_lesson — реальный DB-level FK)."""
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


def test_burned_status_registered():
    assert BURNED == 'burned'
    assert BURNED in STATUS_CHOICES


# --- Task 2: repository ---------------------------------------------------

def test_mark_burned_sets_status_and_fact(absence_pending):
    # fact_lesson_id = missed_lesson_id — валидная строка lessons (для проверки
    # апдейта достаточно; настоящий burned-факт создаёт сервис в Task 3).
    repository.mark_burned(absence_pending.id, fact_lesson_id=absence_pending.missed_lesson_id)
    absence_pending.refresh_from_db()
    assert absence_pending.status == BURNED
    assert absence_pending.fact_lesson_id == absence_pending.missed_lesson_id


def test_has_active_resolution_true_for_burned(absence_pending):
    assert repository.has_active_resolution(
        absence_pending.missed_lesson_id, absence_pending.student_id) is False
    repository.mark_burned(absence_pending.id, fact_lesson_id=absence_pending.missed_lesson_id)
    assert repository.has_active_resolution(
        absence_pending.missed_lesson_id, absence_pending.student_id) is True


# --- Task 3: сервис burn() -------------------------------------------------

def test_burn_creates_burned_fact(absence_pending, payroll_rates):
    student_id = absence_pending.student_id
    missed = Lesson.objects.get(id=absence_pending.missed_lesson_id)

    res = services.burn(absence_pending.id, request=_FakeRequest(), burn_date='2026-07-18')
    absence_pending.refresh_from_db()

    assert absence_pending.status == BURNED
    fact = Lesson.objects.get(id=absence_pending.fact_lesson_id)
    assert fact.lesson_type == 'burned'
    assert fact.lesson_date.isoformat() == '2026-07-18'
    # вес списания = длительность ИСХОДНОГО урока (60), не операционная.
    assert fact.lesson_duration_minutes == missed.lesson_duration_minutes == 60
    assert fact.lesson_number == missed.lesson_number
    # преподаватель пропущенного урока (активен) — преподаватель факта.
    assert fact.teacher_id == missed.teacher_id
    # present=true на burned-факте; ИСХОДНЫЙ пропуск ОСТАЁТСЯ present=false.
    assert LessonAttendance.objects.get(
        lesson_id=fact.id, student_id=student_id).present is True
    assert LessonAttendance.objects.get(
        lesson_id=missed.id, student_id=student_id).present is False
    # Сгорание не оплачивается (спека 2026-10-20 §1.10): 0 даже при действующих
    # ставках, правило названо в rule_note.
    pr = Payroll.objects.get(lesson_id=fact.id)
    assert pr.payment == 0 and pr.penalty == 0 and pr.teacher_id == missed.teacher_id
    assert pr.rule_note == 'Сгорание — не оплачивается'
    assert res['payment'] == 0 and res['lesson_id'] == fact.id


def test_burn_requires_pending(absence_pending):
    services.burn(absence_pending.id, request=_FakeRequest(), burn_date='2026-07-18')
    with pytest.raises(ValueError):
        services.burn(absence_pending.id, request=_FakeRequest(), burn_date='2026-07-18')


def test_burn_missing_returns_none():
    assert services.burn(999_999_999, request=_FakeRequest(), burn_date='2026-07-18') is None


def test_burn_pays_current_group_teacher_when_missed_teacher_fired(
    absence_pending, other_teacher_fixture,
):
    """Уволенному преподавателю пропущенного урока платить нельзя — надбавка
    уходит текущему преподавателю группы (Group.teacher_id)."""
    from apps.groups.models import Group
    from apps.teachers.models import Teacher

    missed = Lesson.objects.get(id=absence_pending.missed_lesson_id)
    original_teacher_id = missed.teacher_id
    # Сделать текущим преподавателем группы ДРУГОГО (активного), а преподавателя
    # пропущенного урока — уволить.
    Group.objects.filter(id=missed.group_id).update(teacher_id=other_teacher_fixture)
    Teacher.objects.filter(id=original_teacher_id).update(active=False)
    try:
        services.burn(absence_pending.id, request=_FakeRequest(), burn_date='2026-07-18')
        absence_pending.refresh_from_db()
        pr = Payroll.objects.get(lesson_id=absence_pending.fact_lesson_id)
        assert pr.teacher_id == other_teacher_fixture
    finally:
        # Вернуть группу исходному преподавателю и снести burned-факт (он
        # references other_teacher_fixture) ДО teardown other_teacher_fixture,
        # иначе FK groups/lessons/payroll → teachers упадёт при удалении препода.
        absence_pending.refresh_from_db()
        if absence_pending.fact_lesson_id:
            _cleanup_fact(absence_pending.fact_lesson_id)
        Group.objects.filter(id=missed.group_id).update(teacher_id=original_teacher_id)
        Teacher.objects.filter(id=original_teacher_id).update(active=True)
        AbsenceResolution.objects.filter(id=absence_pending.id).update(
            status=PENDING, fact_lesson_id=None)


# --- Task 4: откат сгорания через обобщённый delete_fact -------------------

def test_unburn_reverses_everything(absence_pending, lessons_done):
    student_id = absence_pending.student_id
    group_id = Lesson.objects.get(id=absence_pending.missed_lesson_id).group_id
    before = lessons_done(group_id, student_id)

    services.burn(absence_pending.id, request=_FakeRequest(), burn_date='2026-07-18')
    absence_pending.refresh_from_db()
    fact_id = absence_pending.fact_lesson_id
    assert lessons_done(group_id, student_id) == before + 1  # 60-мин пропуск → +1

    ok = services.delete_fact(absence_pending.id, _FakeRequest())
    absence_pending.refresh_from_db()
    assert ok is True
    assert absence_pending.status == PENDING
    assert absence_pending.fact_lesson_id is None
    assert not Lesson.objects.filter(id=fact_id).exists()
    assert not Payroll.objects.filter(lesson_id=fact_id).exists()
    assert lessons_done(group_id, student_id) == before  # lessons_done восстановлен
    # Исходный пропуск всё время present=false.
    assert LessonAttendance.objects.get(
        lesson_id=absence_pending.missed_lesson_id, student_id=student_id).present is False


def test_delete_fact_rejects_pending(absence_pending):
    with pytest.raises(ValueError):
        services.delete_fact(absence_pending.id, _FakeRequest())
