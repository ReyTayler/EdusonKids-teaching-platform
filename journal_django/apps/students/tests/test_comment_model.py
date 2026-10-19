"""Тесты модели StudentComment (без БД — только объявление/метаданные)."""
from __future__ import annotations

from django.db import models

from apps.students.models import StudentComment


def test_table_name():
    assert StudentComment._meta.db_table == 'student_comment'


def test_fields():
    field_names = {f.name for f in StudentComment._meta.get_fields()}
    assert {'id', 'student', 'body', 'author', 'created_at'} <= field_names


def test_author_on_delete_set_null():
    field = StudentComment._meta.get_field('author')
    assert field.remote_field.on_delete is models.SET_NULL


def test_student_on_delete_cascade():
    field = StudentComment._meta.get_field('student')
    assert field.remote_field.on_delete is models.CASCADE


def test_not_pghistory_tracked():
    """
    Осознанное исключение из общего правила CLAUDE.md («каждая новая модель →
    pghistory + registry») — см. docs/superpowers/specs/2026-07-10-student-comments-design.md.
    Комментарий уже self-audit (author+created_at видны в UI), отдельный
    changelog-след избыточен.
    """
    from apps.changelog.registry import TRACKED
    assert 'students.StudentComment' not in TRACKED
