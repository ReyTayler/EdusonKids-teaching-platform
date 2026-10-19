"""
Models for teachers — managed=False, поверх существующей БД.

Таблица:
  teachers — преподаватели (soft-delete через active)

Схема из db/migrations/001_initial_schema.sql + 003_admin_soft_delete.sql.
"""
from __future__ import annotations

import pghistory
from django.db import models


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Teacher(models.Model):
    """
    Преподаватель.

    Соответствует таблице `teachers`.
    """

    id = models.AutoField(primary_key=True)
    name = models.TextField(unique=True)
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    active = models.BooleanField(default=True)
    # Служебная запись, а не живой человек. Такая одна — «Архив (импорт
    # истории)»: на неё повешены архивные группы вида «Python — архив», куда
    # свалили доSheets-историю. На ней висит ~80 % всех продлений школы, поэтому
    # в сравнениях преподавателей между собой она забивает всех живых.
    #
    # Отдельный флаг, а не active=false: запись должна остаться в списках и
    # ссылках (её группы и уроки настоящие), скрывать её нельзя — она лишь не
    # участвует в итогах отчётов как «преподаватель».
    is_service = models.BooleanField(default=False, db_default=False)
    created_at = models.DateTimeField()

    class Meta:
        managed = True
        db_table = 'teachers'
        indexes = [
            models.Index(
                fields=['active'], name='teachers_active_idx',
                condition=models.Q(active=True),
            ),
        ]
