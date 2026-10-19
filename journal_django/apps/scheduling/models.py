"""
Model for scheduling — materialize-on-write плановые занятия.

Таблица:
  planned_lessons  — источник правды по датам/статусам/преподавателю конкретного
                     планового занятия (см. docs/lesson-scheduling.md).

managed=True поверх настоящей Django-миграции (apps/scheduling/migrations/0001_*).
Заменяет прежний compute-on-read из слотов + lesson_schedule_exceptions: строки —
источник правды, операции (перенос/отмена/доп.) мутируют их напрямую.

Конвенции проекта: half-lesson `lesson_number = seq * step` (45мин → step 0.5);
даты — чистый DateField/TimeField без TZ; «сейчас» по МСК (msk_now()).
"""
from __future__ import annotations

import pghistory
from django.db import models

from apps.scheduling.occurrences import (
    CANCELLED, DONE, MOVED, OVERDUE, PENDING,
)

# Допустимые статусы планового занятия (константы из occurrences.py — единый
# источник, не хардкодим строки повторно).
STATUS_CHOICES = [PENDING, OVERDUE, DONE, CANCELLED, MOVED]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class PlannedLesson(models.Model):
    """
    Плановое занятие — материализованная строка расписания группы.

    Соответствует таблице `planned_lessons`.

    Курсовые строки имеют `seq` (1..N) и `lesson_number` (= seq * step); не-курсовые
    (доп. занятие `extra`, маркер отмены) — `seq=NULL` и `lesson_number=NULL`.
    Скоуп календаря — по `teacher_id` конкретного занятия (не по учителю группы):
    смена преподавателя переносит занятие в календарь нового препода.
    """

    id = models.AutoField(primary_key=True)
    group = models.ForeignKey(
        'groups.Group',
        on_delete=models.CASCADE,
        db_column='group_id',
        related_name='planned_lessons',
    )
    # Порядковый номер урока в курсе (1..N); NULL для extra / маркеров отмены.
    seq = models.IntegerField(null=True, blank=True)
    # seq * step (half-lesson); NULL для не-курсовых строк.
    lesson_number = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField()
    # Преподаватель конкретного занятия. По умолчанию = учитель группы; операции
    # переноса/смены могут его менять. Источник правды для скоупа календаря.
    teacher = models.ForeignKey(
        'teachers.Teacher',
        on_delete=models.DO_NOTHING,
        db_column='teacher_id',
        related_name='planned_lessons',
        null=True,
        blank=True,
    )
    # Разовая замена преподавателя НА ЭТУ ДАТУ (не путать с teacher — преподавателем
    # контента). Свойство конкретной календарной даты: при смене scheduled_date
    # строки (перекладка/отмена) обнуляется (замена «не едет» с контентом).
    # Эффективный преподаватель занятия = substitute_teacher или teacher.
    substitute_teacher = models.ForeignKey(
        'teachers.Teacher',
        on_delete=models.DO_NOTHING,
        db_column='substitute_teacher_id',
        related_name='substitute_planned_lessons',
        null=True,
        blank=True,
    )
    status = models.TextField(default=PENDING)
    # Связь план→факт (одна плановая строка ↔ максимум один урок).
    fact_lesson = models.ForeignKey(
        'lessons.Lesson',
        on_delete=models.SET_NULL,
        db_column='fact_lesson_id',
        related_name='planned_lesson',
        null=True,
        blank=True,
        unique=True,
    )
    # Отображение разового переноса: откуда/куда.
    moved_from_date = models.DateField(null=True, blank=True)
    moved_to_date = models.DateField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = True
        db_table = 'planned_lessons'
        indexes = [
            # Основной индекс для операций/плана группы (get_plan, link_facts, cancel).
            models.Index(fields=['group', 'scheduled_date'],
                         name='planned_lessons_group_date_idx'),
            # Скоуп календаря — по teacher_id (равенство) + окно дат. Отдельный
            # композитный индекс НЕ добавляем: Django уже создаёт db_index на FK
            # teacher (ForeignKey → db_index=True), а строк на одного преподавателя
            # немного (курсы ~десятки уроков на пару групп) — фильтр по дате в окне
            # поверх FK-индекса тривиален. См. шаг-5 отчёт.
        ]
        constraints = [
            # Одна строка на позицию курса (seq задан). extra/маркеры (seq NULL)
            # не участвуют в ограничении.
            models.UniqueConstraint(
                fields=['group', 'seq'],
                condition=models.Q(seq__isnull=False),
                name='planned_lessons_group_seq_key',
            ),
            models.CheckConstraint(
                name='planned_lessons_status_check',
                condition=models.Q(status__in=STATUS_CHOICES),
            ),
            # Курсовые строки (seq задан) обязаны иметь lesson_number; не-курсовые
            # (seq NULL) — обязаны иметь lesson_number NULL. Оба заданы ⟺ оба NULL.
            models.CheckConstraint(
                name='planned_lessons_seq_number_together_check',
                condition=(
                    models.Q(seq__isnull=True, lesson_number__isnull=True)
                    | models.Q(seq__isnull=False, lesson_number__isnull=False)
                ),
            ),
        ]
