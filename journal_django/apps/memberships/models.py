"""
Models for memberships — managed=False, поверх существующей БД.

Таблица group_memberships из db/migrations/.
DATE-поле start_date хранится как CharField(max_length=10) для защиты от
timezone drift — та же стратегия, что services/db.js setTypeParser(1082, v => v).
"""
from __future__ import annotations

import pghistory
from django.db import models


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class GroupMembership(models.Model):
    """
    Членство ученика в группе.

    Соответствует таблице `group_memberships`.
    """

    id = models.AutoField(primary_key=True)
    # FK → groups(id) / students(id). NO ACTION в БД → DO_NOTHING (managed=False).
    group = models.ForeignKey(
        'groups.Group',
        on_delete=models.DO_NOTHING,
        db_column='group_id',
        related_name='memberships',
    )
    student = models.ForeignKey(
        'students.Student',
        on_delete=models.DO_NOTHING,
        db_column='student_id',
        related_name='memberships',
    )
    # numeric(6,1) — поддержка half-lesson (0.5), НЕ целое.
    lessons_done = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    start_date = models.DateField(null=True, blank=True)
    sheet_row = models.IntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    # Ссылка на membership, из которой ученик был переведён (apps.memberships.services.transfer_membership).
    # Ставится только сервисом перевода — обычный add_membership её не трогает.
    transferred_from = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_column='transferred_from_id',
        related_name='transferred_to',
    )
    # Колонки created_at в таблице group_memberships НЕТ (см. db/migrations/001).

    class Meta:
        managed = True
        db_table = 'group_memberships'
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'student'],
                name='group_memberships_group_id_student_id_key',
            ),
        ]
        indexes = [
            # Реестр куратора фильтрует membership по одному student_id (attended/
            # planned/codes-подзапросы). Композитный (group_id, student_id) для
            # этого не годится (student_id — не ведущая колонка). См. вариант B.
            models.Index(fields=['student'], name='gm_student_idx'),
        ]
