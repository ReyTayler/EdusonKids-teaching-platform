"""
Отзыв по уроку и админский путь записи (этап 3 Eduson, раздел A).

Админская запись — исправление журнала, а не проведение урока: отзыв она не
требует, не создаёт и уже существующий отзыв не трогает. Обязательность отзыва
живёт только на пути преподавателя (apps.teacher_spa — test_submit_feedback.py).
"""
from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from apps.feedback.models import LessonFeedback
from apps.lessons.tests.test_lessons_api import BASE_URL, _client, _create_lesson, _delete_lesson

pytestmark = pytest.mark.django_db


def test_admin_create_without_feedback(group_fixture, teacher_id_fixture, student_fixture,
                                       membership_fixture):
    resp = _client('admin').post(BASE_URL, {
        'lesson_date': '2026-03-21',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    }, format='json')
    assert resp.status_code == 201, resp.json()
    lesson_id = resp.json()['id']
    try:
        assert not LessonFeedback.objects.filter(lesson_id=lesson_id).exists()
        assert not LessonFeedback.objects.filter(group_id=group_fixture).exists()
    finally:
        _delete_lesson(lesson_id)


def test_admin_patch_keeps_existing_feedback(group_fixture, teacher_id_fixture):
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    fb = LessonFeedback.objects.create(
        group_id=group_fixture, lesson_id=lesson_id, teacher_id=teacher_id_fixture,
        lesson_date=datetime.date(2026, 3, 20), took_place=True, understanding=4,
        interest=6, tech_problems=True, comment='Исходный отзыв',
        submitted_at=timezone.now(),
    )
    snapshot = LessonFeedback.objects.filter(pk=fb.pk).values().get()
    try:
        resp = _client('admin').patch(
            f'{BASE_URL}/{lesson_id}',
            {'lesson_type': 'reschedule', 'record_url': 'https://video.example.test/r'},
            format='json')
        assert resp.status_code == 200, resp.json()
        assert LessonFeedback.objects.filter(pk=fb.pk).values().get() == snapshot
    finally:
        LessonFeedback.objects.filter(pk=fb.pk).delete()
        _delete_lesson(lesson_id)


def test_record_lesson_without_feedback_creates_none(group_fixture, teacher_id_fixture,
                                                     student_fixture, membership_fixture):
    """Ядро без feedback (админ, management-команды) отзыв не заводит."""
    from apps.lessons.services import record_lesson

    result = record_lesson(
        lesson_date='2026-03-22', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='__fb_core_test__',
        submit_date='2026-03-22',
        attendance=[{'student_id': student_fixture, 'present': True}],
    )
    try:
        assert not LessonFeedback.objects.filter(lesson_id=result['lesson_id']).exists()
    finally:
        _delete_lesson(result['lesson_id'])


def test_record_lesson_with_feedback_links_it_to_lesson(group_fixture, teacher_id_fixture,
                                                        student_fixture, membership_fixture):
    from apps.lessons.services import record_lesson

    result = record_lesson(
        lesson_date='2026-03-22', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='__fb_core_test__',
        submit_date='2026-03-22',
        attendance=[{'student_id': student_fixture, 'present': True}],
        feedback={'understanding': 6, 'interest': 7, 'tech_problems': False,
                  'comment': 'Нормально'},
    )
    try:
        fb = LessonFeedback.objects.get(lesson_id=result['lesson_id'])
        assert (fb.group_id, fb.teacher_id, fb.lesson_date) == (
            group_fixture, teacher_id_fixture, datetime.date(2026, 3, 22))
        assert (fb.took_place, fb.understanding, fb.interest, fb.tech_problems, fb.comment) == (
            True, 6, 7, False, 'Нормально')
    finally:
        LessonFeedback.objects.filter(lesson_id=result['lesson_id']).delete()
        _delete_lesson(result['lesson_id'])


def test_admin_patch_date_moves_feedback_date(group_fixture, teacher_id_fixture):
    """Смена даты урока админом переносит дату отзыва этого урока; прочие поля
    отзыва не меняются."""
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    fb = LessonFeedback.objects.create(
        group_id=group_fixture, lesson_id=lesson_id, teacher_id=teacher_id_fixture,
        lesson_date=datetime.date(2026, 3, 20), took_place=True, understanding=4,
        interest=6, tech_problems=False, comment='Отзыв', submitted_at=timezone.now(),
    )
    before = LessonFeedback.objects.filter(pk=fb.pk).values().get()
    try:
        resp = _client('admin').patch(
            f'{BASE_URL}/{lesson_id}', {'lesson_date': '2026-03-24'}, format='json')
        assert resp.status_code == 200, resp.json()
        after = LessonFeedback.objects.filter(pk=fb.pk).values().get()
        assert after['lesson_date'] == datetime.date(2026, 3, 24)
        assert {**after, 'lesson_date': before['lesson_date']} == before
    finally:
        LessonFeedback.objects.filter(pk=fb.pk).delete()
        _delete_lesson(lesson_id)
