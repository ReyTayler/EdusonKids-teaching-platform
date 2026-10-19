"""
Models for students — managed=False, поверх существующей БД.

Таблица students из db/migrations/001_initial_schema.sql.
DATE-поля (birth_date) хранятся как CharField(max_length=10) для защиты от
timezone drift — та же стратегия что в services/db.js setTypeParser(1082, v=>v).
"""
from __future__ import annotations

import pghistory
from django.db import models


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Student(models.Model):
    """
    Ученик.

    Соответствует таблице `students`.
    """

    id = models.AutoField(primary_key=True)
    # Eduson: записано только имя ребёнка («Миша»), тёзки допустимы — ученики
    # различаются по id. Поэтому UNIQUE снят, а сопоставление «по имени» в коде
    # запрещено (см. teacher_spa.services.submit_lesson — там теперь id).
    full_name = models.TextField()
    birth_date = models.DateField(null=True, blank=True)
    platform_id = models.TextField(null=True, blank=True)
    # Было bitrix24_link: у Eduson CRM — amoCRM, имя поля нейтральное.
    crm_link = models.TextField(null=True, blank=True)
    # Контакты самого ребёнка: Eduson шлёт напоминания и ему, не только родителю.
    phone = models.TextField(null=True, blank=True)
    telegram = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    parent1_name = models.TextField(null=True, blank=True)
    parent1_phone = models.TextField(null=True, blank=True)
    parent1_email = models.TextField(null=True, blank=True)
    parent1_telegram = models.TextField(null=True, blank=True)
    parent2_name = models.TextField(null=True, blank=True)
    parent2_phone = models.TextField(null=True, blank=True)
    parent2_email = models.TextField(null=True, blank=True)
    manager = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='manager_id', related_name='managed_students',
    )
    created_at = models.DateTimeField()

    class Meta:
        managed = True
        db_table = 'students'
        indexes = [
            # Раньше btree на full_name давал UNIQUE. Он нужен сортировке списка по
            # умолчанию (ORDER BY full_name, id DESC LIMIT 50 — index scan +
            # incremental sort вместо полной сортировки). Фильтр по имени —
            # icontains (UPPER(..) LIKE '%x%'), его btree не ускоряет ни раньше, ни
            # теперь; pg_trgm в проекте не используется и при сотнях учеников не
            # окупает расширение — seq scan дешевле.
            models.Index(fields=['full_name'], name='students_full_name_idx'),
        ]


class StudentComment(models.Model):
    """
    Комментарий менеджера/админа к ученику. Append-only: без UpdateEvent, без
    API редактирования. Не трекается pghistory — сам факт (author+created_at)
    уже виден в UI, отдельный changelog-след избыточен (осознанное отступление
    от общего правила CLAUDE.md, см. docs/superpowers/specs/2026-07-10-student-comments-design.md).
    """

    id = models.BigAutoField(primary_key=True)
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE,
        db_column='student_id', related_name='comments',
    )
    body = models.TextField()
    author = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='author_id', related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'student_comment'
        indexes = [
            models.Index(fields=['student', '-created_at'], name='student_comment_student_idx'),
        ]
