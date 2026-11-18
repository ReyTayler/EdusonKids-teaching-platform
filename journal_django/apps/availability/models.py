"""
График работы преподавателя. Перенесено из KOTOKOD один в один (исходная спека
2026-11-18-teacher-work-schedule-design.md; план переноса —
docs/plans/2026-11-19-eduson-teacher-work-schedule-port.md в EdusonPlatform).

Недельный шаблон (AvailabilityRule) + исключения на даты (AvailabilityOverride).
Если на дату есть хоть одна строка исключения — она ЦЕЛИКОМ заменяет шаблон дня;
строка с start_time/end_time = NULL означает «день закрыт».

Окна — подсказка для планирования, НЕ блокер постановки уроков (решение владельца).
Сетка: шаг 30 минут, 08:00–22:00; инварианты продублированы CHECK-ами в БД.
"""
from __future__ import annotations

import datetime

import pghistory
from django.db import models
from django.db.models import Q
from django.db.models.functions import TruncMinute

GRID_START = datetime.time(8, 0)
GRID_END = datetime.time(22, 0)

# Сравнение с усечением до минуты, а не __second=0: Django строит __second через
# DATE_TRUNC('second', …), и 10:00:00.5 проходил бы CHECK.
_HALF_HOUR_START = Q(start_time__minute__in=[0, 30]) & Q(start_time=TruncMinute('start_time'))
_HALF_HOUR_END = Q(end_time__minute__in=[0, 30]) & Q(end_time=TruncMinute('end_time'))
_IN_GRID = Q(start_time__gte=GRID_START) & Q(end_time__lte=GRID_END)
_ORDERED = Q(start_time__lt=models.F('end_time'))


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class AvailabilityRule(models.Model):
    """Окно недельного шаблона: day_of_week — конвенция Вс=0 (как group_schedule_slots)."""

    id = models.BigAutoField(primary_key=True)
    teacher = models.ForeignKey('teachers.Teacher', on_delete=models.CASCADE,
                                related_name='availability_rules',
                                # Покрыт префиксом составного avail_rule_teacher_dow_idx.
                                db_index=False)
    day_of_week = models.SmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        managed = True
        db_table = 'teacher_availability_rules'
        indexes = [models.Index(fields=['teacher', 'day_of_week'], name='avail_rule_teacher_dow_idx')]
        constraints = [
            models.CheckConstraint(name='avail_rule_dow_check',
                                   condition=Q(day_of_week__gte=0) & Q(day_of_week__lte=6)),
            models.CheckConstraint(name='avail_rule_ordered', condition=_ORDERED),
            models.CheckConstraint(name='avail_rule_in_grid', condition=_IN_GRID),
            models.CheckConstraint(name='avail_rule_half_hour',
                                   condition=_HALF_HOUR_START & _HALF_HOUR_END),
        ]

    def __str__(self) -> str:
        return f'AvailabilityRule#{self.pk} t={self.teacher_id} d={self.day_of_week} {self.start_time}-{self.end_time}'


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class AvailabilityOverride(models.Model):
    """Исключение на дату. start_time/end_time = NULL → день закрыт целиком."""

    id = models.BigAutoField(primary_key=True)
    teacher = models.ForeignKey('teachers.Teacher', on_delete=models.CASCADE,
                                related_name='availability_overrides',
                                # Покрыт префиксом составного avail_ovr_teacher_date_idx.
                                db_index=False)
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'teacher_availability_overrides'
        indexes = [models.Index(fields=['teacher', 'date'], name='avail_ovr_teacher_date_idx')]
        constraints = [
            models.CheckConstraint(
                name='avail_override_both_or_none',
                condition=(Q(start_time__isnull=True) & Q(end_time__isnull=True))
                | (Q(start_time__isnull=False) & Q(end_time__isnull=False)),
            ),
            models.CheckConstraint(name='avail_override_ordered',
                                   condition=Q(start_time__isnull=True) | _ORDERED),
            models.CheckConstraint(name='avail_override_in_grid',
                                   condition=Q(start_time__isnull=True) | _IN_GRID),
            models.CheckConstraint(name='avail_override_half_hour',
                                   condition=Q(start_time__isnull=True)
                                   | (_HALF_HOUR_START & _HALF_HOUR_END)),
            # Не больше одной строки «день закрыт» на дату.
            models.UniqueConstraint(fields=['teacher', 'date'],
                                    condition=Q(start_time__isnull=True),
                                    name='avail_override_one_closed_per_date'),
        ]

    def __str__(self) -> str:
        return f'AvailabilityOverride#{self.pk} t={self.teacher_id} {self.date} {self.start_time}-{self.end_time}'
