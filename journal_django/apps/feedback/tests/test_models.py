"""Инварианты LessonFeedback на уровне БД: обязательные группа и дата, оценки 1–10,
один отзыв на урок."""
from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db

DAY = datetime.date(2026, 1, 19)


def _assert_rejected(make_feedback, **kwargs):
    # savepoint: после IntegrityError транзакция теста остаётся рабочей
    with pytest.raises(IntegrityError), transaction.atomic():
        make_feedback(**kwargs)


def test_group_required(school, make_feedback):
    _assert_rejected(make_feedback, teacher_id=school['teacher_id'], lesson_date=DAY)


def test_lesson_date_required(school, make_feedback):
    _assert_rejected(make_feedback, teacher_id=school['teacher_id'], group_id=school['group_id'])


@pytest.mark.parametrize('field', ['understanding', 'interest'])
@pytest.mark.parametrize('value', [0, 11])
def test_score_out_of_range_rejected(school, make_feedback, field, value):
    _assert_rejected(
        make_feedback, teacher_id=school['teacher_id'], group_id=school['group_id'],
        lesson_date=DAY, **{field: value},
    )


@pytest.mark.parametrize('value', [1, 10])
def test_score_bounds_accepted(school, held, value):
    fb = held(school['lesson_id'], understanding=value, interest=value)
    fb.refresh_from_db()
    assert (fb.understanding, fb.interest) == (value, value)


def test_second_feedback_on_same_lesson_rejected(school, held):
    held(school['lesson_id'])
    with pytest.raises(IntegrityError), transaction.atomic():
        held(school['lesson_id'])


def test_feedback_when_lesson_not_held(school, not_held):
    """Урок не состоялся: факта нет, отзыв держится на группе и плановой дате."""
    from apps.feedback.models import LessonFeedback

    fb = not_held(school['group_id'], school['teacher_id'], DAY, comment='Ученик заболел')
    stored = LessonFeedback.objects.get(pk=fb.pk)
    assert stored.lesson_id is None
    assert (stored.group_id, stored.lesson_date) == (school['group_id'], DAY)
    assert stored.took_place is False
    assert stored.understanding is None and stored.interest is None


def test_several_not_held_feedbacks_in_same_group(school, not_held):
    """Без урока уникальности нет: NULL lesson_id в Postgres не конфликтуют."""
    not_held(school['group_id'], school['teacher_id'], DAY)
    not_held(school['group_id'], school['teacher_id'], DAY + datetime.timedelta(days=7))


def test_group_and_date_independent_of_lesson(school, other_group, make_feedback):
    """Документ модели: БД НЕ сверяет group/lesson_date с уроком (CHECK не видит
    другую таблицу). Согласованность — обязанность импорта и сервиса записи."""
    fb = make_feedback(teacher_id=school['teacher_id'], lesson_id=school['lesson_id'],
                       group_id=other_group['group_id'], lesson_date=DAY)
    fb.refresh_from_db()
    assert fb.group_id == other_group['group_id']
    assert fb.lesson.group_id == school['group_id']
    assert fb.lesson_date != fb.lesson.lesson_date


def test_feedback_survives_lesson_deletion(school, held):
    """Удаление урока (ORM, как delete_lesson_full) не уносит отзыв: он держится на
    группе и дате, ссылка на урок обнуляется."""
    from apps.lessons.models import Lesson, LessonAttendance

    fb = held(school['lesson_id'])
    LessonAttendance.objects.filter(lesson_id=school['lesson_id']).delete()
    Lesson.objects.filter(id=school['lesson_id']).delete()
    fb.refresh_from_db()
    assert fb.lesson_id is None
    assert (fb.group_id, fb.lesson_date) == (school['group_id'], datetime.date(2026, 1, 12))


def test_not_held_feedback_survives_plan_reset(school, not_held):
    """Ради этого отзыв и не ссылается на строку плана: пересбор плана группы
    (reset_plan) удаляет позиции, но не отзывы о несостоявшихся уроках."""
    from apps.feedback.models import LessonFeedback
    from apps.scheduling.repository import reset_plan

    fb = not_held(school['group_id'], school['teacher_id'], DAY)
    reset_plan(school['group_id'])
    assert LessonFeedback.objects.filter(pk=fb.pk).exists()


def test_feedback_is_tracked_by_changelog(school, held):
    """Создание отзыва пишет событие в журнал изменений (pghistory)."""
    from apps.feedback.models import LessonFeedbackEvent

    fb = held(school['lesson_id'])
    assert LessonFeedbackEvent.objects.filter(pgh_obj_id=fb.pk, pgh_label='insert').exists()
