"""
Models for discounts — managed=False, поверх существующей БД.

Таблица:
  discounts — скидки (soft-delete через active)

Схема из db/migrations/011_discounts.sql.
"""
from __future__ import annotations

import pghistory
from django.db import models


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Discount(models.Model):
    """
    Скидка.

    Соответствует таблице `discounts`.
    amount: numeric(5,4) — от 0 до 1 (доля, не проценты).
    """

    id = models.AutoField(primary_key=True)
    name = models.TextField()
    amount = models.DecimalField(max_digits=5, decimal_places=4)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = True
        db_table = 'discounts'
        indexes = [
            models.Index(fields=['active'], name='discounts_active_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                name='discounts_amount_check',
                condition=models.Q(amount__gte=0) & models.Q(amount__lte=1),
            ),
        ]
