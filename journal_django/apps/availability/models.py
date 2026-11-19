"""
График работы преподавателя. Перенесено из KOTOKOD один в один (исходная спека
2026-11-18-teacher-work-schedule-design.md; план переноса —
docs/plans/2026-11-19-eduson-teacher-work-schedule-port.md в EdusonPlatform).

Недельный шаблон (AvailabilityRule) + исключения на даты (AvailabilityOverride).
Если на дату есть хоть одна строка исключения — она ЦЕЛИКОМ заменяет шаблон дня;
строка с start_minute/end_minute = NULL означает «день закрыт».

Окна — подсказка для планирования, НЕ блокер постановки уроков (решение владельца).
Сетка: сутки 00:00–24:00, шаг 30 минут; инварианты продублированы CHECK-ами в БД.

Время хранится минутами от полуночи (0–1440), а не TimeField: TimeField не
умеет «24:00», а окно до конца суток — законный случай (спека 2026-11-19).
Наружу (API) время по-прежнему уходит строкой "HH:MM", 1440 → "24:00".
"""
from __future__ import annotations

import pghistory
from django.db import models
from django.db.models import Q
from django.db.models.functions import Mod
from django.db.models.lookups import Exact

GRID_START_MIN = 0
GRID_END_MIN = 24 * 60
SLOT_MIN = 30

# У Q нет lookup «кратно» — выражение MOD(col, 30) = 0 собирается lookup'ом
# Exact поверх функции Mod (Django ≥ 4.0 принимает lookup-выражения в условиях).
_ON_SLOT = (Q(Exact(Mod('start_minute', SLOT_MIN), 0))
            & Q(Exact(Mod('end_minute', SLOT_MIN), 0)))
_IN_GRID = Q(start_minute__gte=GRID_START_MIN) & Q(end_minute__lte=GRID_END_MIN)
_ORDERED = Q(start_minute__lt=models.F('end_minute'))


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class AvailabilityRule(models.Model):
    """Окно недельного шаблона: day_of_week — конвенция Вс=0 (как group_schedule_slots)."""

    id = models.BigAutoField(primary_key=True)
    teacher = models.ForeignKey('teachers.Teacher', on_delete=models.CASCADE,
                                related_name='availability_rules',
                                # Покрыт префиксом составного avail_rule_teacher_dow_idx.
                                db_index=False)
    day_of_week = models.SmallIntegerField()
    start_minute = models.SmallIntegerField()
    end_minute = models.SmallIntegerField()

    class Meta:
        managed = True
        db_table = 'teacher_availability_rules'
        indexes = [models.Index(fields=['teacher', 'day_of_week'], name='avail_rule_teacher_dow_idx')]
        constraints = [
            models.CheckConstraint(name='avail_rule_dow_check',
                                   condition=Q(day_of_week__gte=0) & Q(day_of_week__lte=6)),
            models.CheckConstraint(name='avail_rule_ordered', condition=_ORDERED),
            models.CheckConstraint(name='avail_rule_in_grid', condition=_IN_GRID),
            models.CheckConstraint(name='avail_rule_half_hour', condition=_ON_SLOT),
        ]

    def __str__(self) -> str:
        return f'AvailabilityRule#{self.pk} t={self.teacher_id} d={self.day_of_week} {self.start_minute}-{self.end_minute}'


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class AvailabilityOverride(models.Model):
    """Исключение на дату. start_minute/end_minute = NULL → день закрыт целиком."""

    id = models.BigAutoField(primary_key=True)
    teacher = models.ForeignKey('teachers.Teacher', on_delete=models.CASCADE,
                                related_name='availability_overrides',
                                # Покрыт префиксом составного avail_ovr_teacher_date_idx.
                                db_index=False)
    date = models.DateField()
    start_minute = models.SmallIntegerField(null=True, blank=True)
    end_minute = models.SmallIntegerField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'teacher_availability_overrides'
        indexes = [models.Index(fields=['teacher', 'date'], name='avail_ovr_teacher_date_idx')]
        constraints = [
            models.CheckConstraint(
                name='avail_override_both_or_none',
                condition=(Q(start_minute__isnull=True) & Q(end_minute__isnull=True))
                | (Q(start_minute__isnull=False) & Q(end_minute__isnull=False)),
            ),
            models.CheckConstraint(name='avail_override_ordered',
                                   condition=Q(start_minute__isnull=True) | _ORDERED),
            models.CheckConstraint(name='avail_override_in_grid',
                                   condition=Q(start_minute__isnull=True) | _IN_GRID),
            models.CheckConstraint(name='avail_override_half_hour',
                                   condition=Q(start_minute__isnull=True) | _ON_SLOT),
            # Не больше одной строки «день закрыт» на дату.
            models.UniqueConstraint(fields=['teacher', 'date'],
                                    condition=Q(start_minute__isnull=True),
                                    name='avail_override_one_closed_per_date'),
        ]

    def __str__(self) -> str:
        return f'AvailabilityOverride#{self.pk} t={self.teacher_id} {self.date} {self.start_minute}-{self.end_minute}'
