"""
Фикстуры тестов feedback.

django_db_setup НЕ переопределяем (как renewals/reports/taskboard): таблицу
lesson_feedback создаёт только миграция этого приложения, поэтому тесты идут в
свежую test_eduson_test, которую pytest-django собирает из миграций. Так набор
работает и отдельно, и в полном прогоне, не завися от того, догнана ли общая
eduson_test до последней миграции.

Данные — свои: ensure_minimal_school() и строки ниже живут в транзакции теста и
откатываются вместе с ней, на содержимое БД тесты не полагаются.
"""
from __future__ import annotations

import datetime

import pytest
from django.utils import timezone


@pytest.fixture
def school(db):
    """Минимальная школа: группа, преподаватель и проведённый урок 2026-01-12."""
    from apps.core.tests.minimal_school import ensure_minimal_school

    return ensure_minimal_school()


@pytest.fixture
def other_group(db, school):
    """Чужая группа со своим уроком — для проверки фильтров."""
    from apps.groups.models import Group
    from apps.lessons.models import Lesson

    now = timezone.now()
    group = Group.objects.create(
        name='Тест Группа 2', direction_id=school['direction_id'],
        teacher_id=school['teacher_id'], is_individual=False,
        lesson_duration_minutes=90, lessons_per_week=1, active=True, created_at=now,
    )
    lesson = Lesson.objects.create(
        group=group, teacher_id=school['teacher_id'], lesson_date=datetime.date(2026, 1, 13),
        lesson_number=1, lesson_duration_minutes=90, lesson_type='regular',
        submitted_at=now, submitted_by_token='__feedback_test__',
    )
    return {'group_id': group.id, 'lesson_id': lesson.id}


@pytest.fixture
def make_feedback():
    """Фабрика отзыва: по умолчанию урок состоялся, оценки в диапазоне.

    held(target) / not_held(target, day) — отзыв на проведённый урок (группа и дата
    берутся с урока) и на несостоявшийся (только группа + плановая дата)."""
    from apps.feedback.models import LessonFeedback

    def _make(**kwargs):
        defaults = {
            'took_place': True, 'understanding': 8, 'interest': 9,
            'tech_problems': False, 'comment': '',
            'submitted_at': timezone.now(),
        }
        defaults.update(kwargs)
        return LessonFeedback.objects.create(**defaults)

    return _make


@pytest.fixture
def held(make_feedback):
    """Отзыв на проведённый урок: группа и дата — с самого урока, как сделает импорт."""
    from apps.lessons.models import Lesson

    def _held(lesson_id: int, **kwargs):
        lesson = Lesson.objects.get(pk=lesson_id)
        kwargs.setdefault('teacher_id', lesson.teacher_id)
        return make_feedback(lesson_id=lesson.id, group_id=lesson.group_id,
                             lesson_date=lesson.lesson_date, **kwargs)

    return _held


@pytest.fixture
def not_held(make_feedback):
    """Отзыв на несостоявшийся урок: факта нет — только группа и плановая дата."""

    def _not_held(group_id: int, teacher_id: int, day: datetime.date, **kwargs):
        kwargs.setdefault('took_place', False)
        kwargs.setdefault('understanding', None)
        kwargs.setdefault('interest', None)
        return make_feedback(group_id=group_id, teacher_id=teacher_id, lesson_date=day,
                             lesson_id=None, **kwargs)

    return _not_held
