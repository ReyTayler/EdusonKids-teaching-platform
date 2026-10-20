"""Отзыв по уроку в журнале изменений: реестр, подпись и откат операций с уроком."""
from __future__ import annotations

import pghistory
import pytest
from django.apps import apps

from apps.changelog import humanize, registry, revert, summary


def test_registered_after_groups_and_lessons():
    cfg = registry.TRACKED['feedback.LessonFeedback']
    assert cfg.entity == 'lesson_feedback'
    assert cfg.revertable is True
    # при откате удаления группа и урок должны встать раньше отзыва
    assert cfg.topo > registry.TRACKED['groups.Group'].topo
    assert cfg.topo > registry.TRACKED['lessons.Lesson'].topo


def test_title_names_group_and_date():
    lk = summary.Lookups(groups={5: 'Python-1'})
    ev = {'entity': 'lesson_feedback', 'pgh_obj_id': 1,
          'pgh_data': {'group_id': 5, 'teacher_id': 7, 'lesson_date': '2026-01-12'}}
    assert humanize.humanize_title(ev, lk) == 'Отзыв по уроку — Python-1, 2026-01-12'


def _last_lesson_ctx(lesson_id: int):
    ev = apps.get_model('lessons', 'LessonEvent')
    return (ev.objects.filter(pgh_obj_id=lesson_id, pgh_context_id__isnull=False)
            .order_by('-pgh_id').first().pgh_context_id)


@pytest.mark.django_db
def test_revert_lesson_delete_restores_feedback_link(school, held):
    """Удаление урока обнулило ссылку (UpdateEvent в том же контексте) → откат
    удаления возвращает урок, а undo-update — ссылку отзыва на него."""
    from apps.feedback.models import LessonFeedback
    from apps.lessons import services as lessons_services
    from apps.lessons.models import Lesson

    fb = held(school['lesson_id'])
    with pghistory.context(url='/api/admin/lessons/1', method='DELETE'):
        assert lessons_services.delete_lesson_full(school['lesson_id'])
    assert LessonFeedback.objects.get(pk=fb.pk).lesson_id is None

    revert.revert_context(_last_lesson_ctx(school['lesson_id']))

    assert Lesson.objects.filter(pk=school['lesson_id']).exists()
    assert LessonFeedback.objects.get(pk=fb.pk).lesson_id == school['lesson_id']


@pytest.mark.django_db
def test_revert_lesson_record_keeps_feedback_from_other_operation(school, held):
    """Урок записан одной операцией, отзыв создан другой → откат записи урока
    удаляет урок, но не чужой отзыв: он остаётся на группе и дате."""
    import datetime

    from django.utils import timezone

    from apps.feedback.models import LessonFeedback
    from apps.lessons.models import Lesson

    day = datetime.date(2026, 1, 19)
    with pghistory.context(url='/api/admin/lessons', method='POST'):
        lesson = Lesson.objects.create(
            group_id=school['group_id'], teacher_id=school['teacher_id'], lesson_date=day,
            lesson_number=2, lesson_duration_minutes=90, lesson_type='regular',
            submitted_at=timezone.now(), submitted_by_token='__feedback_revert__',
        )
    with pghistory.context(url='/import', method='POST'):
        fb = held(lesson.id)

    revert.revert_context(_last_lesson_ctx(lesson.id))

    assert not Lesson.objects.filter(pk=lesson.id).exists()
    stored = LessonFeedback.objects.get(pk=fb.pk)
    assert stored.lesson_id is None
    assert (stored.group_id, stored.lesson_date) == (school['group_id'], day)


@pytest.mark.django_db
def test_revert_teacher_record_removes_lesson_and_its_feedback(school, present_classmate):
    """Преподаватель записал урок с отзывом — одна операция «lesson.submit».
    Её откат убирает и урок, и отзыв: отзыв без урока, ради которого его
    написали, — висящая запись «состоялся» без факта."""
    from apps.feedback.models import LessonFeedback
    from apps.lessons.models import Lesson
    from apps.lessons.services import record_lesson

    # Пришедший ученик заводится ДО операции: урок без присутствующих не
    # записывается, а сам одноклассник не часть отката.
    attendance = present_classmate([], school['group_id'])
    with pghistory.context(url='/api/submitLesson', method='POST'):
        result = record_lesson(
            lesson_date='2026-01-20', teacher_id=school['teacher_id'],
            group_id=school['group_id'], original_teacher_id=None, lesson_number=2,
            lesson_duration_minutes=90, lesson_type='regular', record_url=None,
            submitted_by_token='__feedback_revert_submit__',
            attendance=attendance,
            feedback={'understanding': 5, 'interest': 6, 'tech_problems': False,
                      'comment': 'Тест'},
        )
    lesson_id = result['lesson_id']
    assert LessonFeedback.objects.filter(lesson_id=lesson_id).exists()

    revert.revert_context(_last_lesson_ctx(lesson_id))

    assert not Lesson.objects.filter(pk=lesson_id).exists()
    assert not LessonFeedback.objects.filter(group_id=school['group_id'],
                                             lesson_date='2026-01-20').exists()


@pytest.mark.django_db
def test_lesson_date_change_moves_feedback_and_revert_restores_both(school, held, other_group):
    """Правка даты урока переносит дату его отзыва в той же операции; откат
    операции из журнала возвращает обе даты. Чужой отзыв (другой урок) не трогается."""
    import datetime

    from apps.feedback.models import LessonFeedback
    from apps.lessons import services as lessons_services
    from apps.lessons.models import Lesson

    fb = held(school['lesson_id'])
    foreign = held(other_group['lesson_id'])
    new_day = datetime.date(2026, 1, 15)
    with pghistory.context(url=f"/api/admin/lessons/{school['lesson_id']}", method='PATCH'):
        lessons_services.update_lesson(school['lesson_id'], {'lesson_date': new_day})

    assert LessonFeedback.objects.get(pk=fb.pk).lesson_date == new_day
    assert LessonFeedback.objects.get(pk=foreign.pk).lesson_date == datetime.date(2026, 1, 13)

    revert.revert_context(_last_lesson_ctx(school['lesson_id']))

    old_day = datetime.date(2026, 1, 12)
    assert Lesson.objects.get(pk=school['lesson_id']).lesson_date == old_day
    assert LessonFeedback.objects.get(pk=fb.pk).lesson_date == old_day
