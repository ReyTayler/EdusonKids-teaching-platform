"""Запросы к lesson_feedback (чтение для admin-раздела)."""
from __future__ import annotations

import datetime

from django.db.models import QuerySet

from apps.feedback.models import LessonFeedback


def list_feedback(
    *, group_id: int | None = None, teacher_id: int | None = None,
    lesson_id: int | None = None, date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
) -> QuerySet[LessonFeedback]:
    """Отзывы новые сверху; id — тай-брейк, чтобы страницы не «ехали» при равном
    submitted_at (импорт пишет отзывы пачками с одинаковым временем).

    Период — по lesson_date, границы включительно. С фильтром группы его
    обслуживает lesson_feedback_group_date_idx; без группы — лента идёт по
    lesson_feedback_submitted_idx с фильтром (порядок всё равно по submitted_at)."""
    # Имя преподавателя, код группы и ссылка на запись урока — в ответе ленты;
    # JOIN'ами, чтобы число запросов не росло со страницей. lesson — LEFT JOIN
    # (у отзыва «не состоялся» урока нет).
    qs = LessonFeedback.objects.select_related('teacher', 'group', 'lesson')
    if group_id is not None:
        qs = qs.filter(group_id=group_id)
    if teacher_id is not None:
        qs = qs.filter(teacher_id=teacher_id)
    if lesson_id is not None:
        qs = qs.filter(lesson_id=lesson_id)
    if date_from is not None:
        qs = qs.filter(lesson_date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(lesson_date__lte=date_to)
    return qs.order_by('-submitted_at', '-id')
