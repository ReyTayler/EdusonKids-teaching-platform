"""Запросы к lesson_feedback (чтение для admin-раздела)."""
from __future__ import annotations

from django.db.models import QuerySet

from apps.feedback.models import LessonFeedback


def list_feedback(
    *, group_id: int | None = None, teacher_id: int | None = None,
    lesson_id: int | None = None,
) -> QuerySet[LessonFeedback]:
    """Отзывы новые сверху; id — тай-брейк, чтобы страницы не «ехали» при равном
    submitted_at (импорт пишет отзывы пачками с одинаковым временем)."""
    # lesson в ответе не читается (group/lesson_date — поля самого отзыва),
    # поэтому подтягиваем только teacher — ради teacher_name.
    qs = LessonFeedback.objects.select_related('teacher')
    if group_id is not None:
        qs = qs.filter(group_id=group_id)
    if teacher_id is not None:
        qs = qs.filter(teacher_id=teacher_id)
    if lesson_id is not None:
        qs = qs.filter(lesson_id=lesson_id)
    return qs.order_by('-submitted_at', '-id')
