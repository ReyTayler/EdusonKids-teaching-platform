"""Соответствие записей старой системы Eduson записям платформы.

Пишут оба импорта: полный (import_eduson) и мягкий (sync_eduson). По этой таблице
мягкое обновление узнаёт «ту же» группу, того же ученика или преподавателя и обновляет
их на месте — id в платформе не меняются, и всё, что на них ссылается (сделки
продлений, карточки задач, ссылки на страницы), остаётся целым.

Таблица техническая, не доменная: строка — следствие импорта, откатывать её отдельно
бессмысленно, поэтому pghistory и запись в apps/changelog/registry.py ей не нужны.
"""
from __future__ import annotations

from django.db import models


class EdusonLink(models.Model):
    class Kind(models.TextChoices):
        DIRECTION = 'direction', 'Направление'     # source_id — ключ курса Eduson ('python'…)
        TEACHER = 'teacher', 'Преподаватель'       # teachers.id
        ACCOUNT = 'account', 'Учётка'              # users.id
        GROUP = 'group', 'Группа'                  # student_groups.id
        PARTICIPANT = 'participant', 'Участник'    # participants.id → Student.id (склейка: много → один)

    kind = models.CharField(max_length=16, choices=Kind.choices)
    source_id = models.CharField(max_length=64)
    object_id = models.IntegerField()

    class Meta:
        db_table = 'eduson_links'
        constraints = [
            models.UniqueConstraint(fields=['kind', 'source_id'], name='eduson_links_kind_source_uniq'),
        ]
        indexes = [
            models.Index(fields=['kind', 'object_id'], name='eduson_links_kind_object_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.kind}:{self.source_id} → {self.object_id}'
