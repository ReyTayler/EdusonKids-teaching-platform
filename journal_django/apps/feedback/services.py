"""Доменные операции с отзывами по урокам."""
from __future__ import annotations

from django.utils import timezone

from apps.feedback.models import LessonFeedback


def create_for_recorded_lesson(
    *, lesson_id: int, group_id: int, teacher_id: int, lesson_date,
    understanding: int, interest: int, tech_problems: bool, comment: str,
) -> LessonFeedback:
    """Отзыв преподавателя на только что записанный урок.

    Вызывается ТОЛЬКО изнутри транзакции записи урока
    (apps.lessons.services.record_lesson): отзыв и урок появляются вместе или не
    появляются оба. group/lesson_date/teacher берутся с урока, а не от клиента —
    согласованность отзыва с уроком БД проверить не может (см. models.py).
    Записанный урок — состоявшийся по определению, поэтому took_place=True.

    Второго отзыва на урок быть не может: OneToOne на lesson даёт UNIQUE, а
    повтор записи того же занятия отсекается раньше, на захвате позиции/ключе
    отправки урока (LessonAlreadyRecorded), до этой функции он не доходит.
    """
    return LessonFeedback.objects.create(
        lesson_id=lesson_id,
        group_id=group_id,
        teacher_id=teacher_id,
        lesson_date=lesson_date,
        took_place=True,
        understanding=understanding,
        interest=interest,
        tech_problems=tech_problems,
        comment=comment,
        submitted_at=timezone.now(),
    )


def sync_lesson_date(lesson_id: int, lesson_date) -> int:
    """Перенести дату отзыва вслед за датой его урока (правка урока админом).

    Отзыв держится на group + lesson_date (см. models.py), поэтому дата обязана
    совпадать с уроком, иначе отзыв уезжает от своего занятия в ленте и в
    сопоставлении с планом. Вызывать внутри транзакции правки урока: UPDATE
    пишет UpdateEvent pghistory в тот же контекст, и откат операции из журнала
    возвращает обе даты. Отзывы без урока («не состоялся») не затрагиваются.
    Возвращает число обновлённых строк (0 или 1 — OneToOne на lesson).
    """
    return (LessonFeedback.objects
            .filter(lesson_id=lesson_id)
            .exclude(lesson_date=lesson_date)
            .update(lesson_date=lesson_date))
