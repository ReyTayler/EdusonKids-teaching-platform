"""
Models for directions — managed=False, поверх существующей БД.

Таблица:
  directions — направления обучения (soft-delete через active)

Схема из db/migrations/001_initial_schema.sql + 003_admin_soft_delete.sql +
  004_directions_total_lessons.sql + 005_directions_color.sql +
  007_directions_subscription_price.sql.
"""
from __future__ import annotations

import pghistory
from django.db import models


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Direction(models.Model):
    """
    Направление обучения.

    Соответствует таблице `directions`.
    """

    id = models.AutoField(primary_key=True)
    name = models.TextField(unique=True)
    total_lessons = models.IntegerField(null=True, blank=True)
    color = models.TextField(null=True, blank=True)
    subscription_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    active = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'directions'
        indexes = [
            models.Index(
                fields=['active'], name='directions_active_idx',
                condition=models.Q(active=True),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                name='directions_total_lessons_check',
                condition=models.Q(total_lessons__isnull=True) | models.Q(total_lessons__gte=0),
            ),
            models.CheckConstraint(
                name='directions_color_check',
                condition=models.Q(color__isnull=True) | models.Q(color__regex=r'^#[0-9a-fA-F]{6}$'),
            ),
            models.CheckConstraint(
                name='directions_subscription_price_check',
                condition=models.Q(subscription_price__isnull=True) | models.Q(subscription_price__gte=0),
            ),
        ]
