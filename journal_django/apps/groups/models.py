"""
Models for groups — managed=False, поверх существующей БД.

Таблицы:
  groups                — группы занятий (soft-delete через active)
  group_schedule_slots  — расписание (день недели + время начала)

Схема из db/migrations/001_initial_schema.sql + 003_admin_soft_delete.sql.
date-поля хранятся как CharField(max_length=10) — защита от timezone drift
(та же стратегия, что services/db.js setTypeParser(1082, v => v)).
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pghistory
from django.db import models

# Sentinel «действует с начала времён» для effective_from слотов без явной даты
# (та же семантика, что «слот активен всегда»). НЕ NULL: см. gss_versioned_key.
SLOT_EFFECTIVE_SENTINEL = datetime.date(2000, 1, 1)


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Group(models.Model):
    """
    Группа занятий.

    Соответствует таблице `groups`.
    """

    id = models.AutoField(primary_key=True)
    name = models.TextField(unique=True)
    # FK → directions(id) / teachers(id). NO ACTION в БД → DO_NOTHING (managed=False).
    direction = models.ForeignKey(
        'directions.Direction',
        on_delete=models.DO_NOTHING,
        db_column='direction_id',
        related_name='groups',
    )
    teacher = models.ForeignKey(
        'teachers.Teacher',
        on_delete=models.DO_NOTHING,
        db_column='teacher_id',
        related_name='groups',
    )
    is_individual = models.BooleanField()
    lesson_duration_minutes = models.IntegerField(default=90)
    lessons_per_week = models.IntegerField(default=1)
    group_start_date = models.DateField(null=True, blank=True)
    vk_chat = models.TextField(null=True, blank=True)
    # Длина курса ИМЕННО ЭТОЙ группы в уроках. NULL — «как в направлении»
    # (directions.total_lessons), это дефолт и поведение всех старых групп.
    # Задаётся вручную в форме группы, когда группа должна отходить только часть
    # курса (ученик начал не с первого урока — прошёл остальное в другой группе).
    # Единица — УРОКИ, не занятия: у 45-мин группы шаг 0.5, поэтому 2 урока = 4
    # занятия. Читать только через apps.groups.course_length — длина группы НЕ
    # применяется к лимиту продаж (apps.payments) и к прогрессу ученика по
    # направлению (apps.students). См.
    # docs/superpowers/specs/2026-07-27-group-lessons-total-design.md.
    lessons_total = models.IntegerField(null=True, blank=True)
    # Фаза 1b transfer-progress-alignment: если группа была продолжена курсом
    # переведённого ученика (см. apps.memberships.repository.place_student_in_group),
    # здесь хранится B (сколько уроков ученик уже отработал в старой группе) —
    # план группы (apps.scheduling) генерируется начиная с этого номера, не с 0.
    lesson_number_offset = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal('0'))
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = True
        db_table = 'groups'
        indexes = [
            models.Index(
                fields=['active'], name='groups_active_idx',
                condition=models.Q(active=True),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                name='groups_lesson_duration_minutes_check',
                condition=models.Q(lesson_duration_minutes__in=[45, 60, 90]),
            ),
            models.CheckConstraint(
                name='groups_lessons_per_week_check',
                condition=models.Q(lessons_per_week__gte=1) & models.Q(lessons_per_week__lte=7),
            ),
            models.CheckConstraint(
                name='groups_lessons_total_check',
                condition=models.Q(lessons_total__isnull=True) | models.Q(lessons_total__gt=0),
            ),
        ]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class GroupScheduleSlot(models.Model):
    """
    Слот расписания группы (версионируемый по датам действия).

    Соответствует таблице `group_schedule_slots`.

    day_of_week: 0–6, конвенция **Вс=0** (JS getDay) — проверено на реальных
    данных. effective_from/effective_to задают период действия слота:
    слот активен на дату D, если effective_from <= D и (effective_to IS NULL
    или D <= effective_to). Постоянная смена времени = закрыть текущие открытые
    слоты (effective_to = дата-1) и вставить новые (effective_from = дата).
    """

    id = models.AutoField(primary_key=True)
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        db_column='group_id',
        related_name='schedule_slots',
    )
    day_of_week = models.IntegerField()
    start_time = models.TimeField()
    effective_from = models.DateField(default=SLOT_EFFECTIVE_SENTINEL)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'group_schedule_slots'
        indexes = [
            models.Index(fields=['day_of_week', 'start_time'],
                         name='gss_dow_time_idx'),
            models.Index(fields=['group', 'effective_from', 'effective_to'],
                         name='gss_group_effective_idx'),
        ]
        constraints = [
            # Версионный ключ: тот же день/время может повторяться в разных
            # непересекающихся периодах (после постоянной смены расписания).
            models.UniqueConstraint(
                fields=['group', 'day_of_week', 'start_time', 'effective_from'],
                name='gss_versioned_key',
            ),
            models.CheckConstraint(
                name='group_schedule_slots_day_of_week_check',
                condition=models.Q(day_of_week__gte=0) & models.Q(day_of_week__lte=6),
            ),
            models.CheckConstraint(
                name='gss_effective_range_check',
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F('effective_from')),
            ),
        ]
