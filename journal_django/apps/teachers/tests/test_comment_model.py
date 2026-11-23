"""Тесты модели TeacherComment (без БД — только объявление/метаданные)."""
from __future__ import annotations

from django.db import models

from apps.teachers.models import Teacher, TeacherComment


def test_table_name():
    assert TeacherComment._meta.db_table == 'teacher_comment'


def test_fields():
    field_names = {f.name for f in TeacherComment._meta.get_fields()}
    assert {'id', 'teacher', 'body', 'author', 'created_at'} <= field_names


def test_teacher_on_delete_cascade_and_related_name():
    field = TeacherComment._meta.get_field('teacher')
    assert field.remote_field.on_delete is models.CASCADE
    assert field.remote_field.model is Teacher
    assert field.remote_field.related_name == 'comments'


def test_author_on_delete_set_null():
    field = TeacherComment._meta.get_field('author')
    assert field.remote_field.on_delete is models.SET_NULL
    assert field.null is True


def test_index_teacher_created_at_desc():
    index_fields = [tuple(idx.fields) for idx in TeacherComment._meta.indexes]
    assert ('teacher', '-created_at') in index_fields


def test_not_pghistory_tracked():
    """
    То же осознанное исключение, что у StudentComment: комментарий сам по себе
    self-audit (author+created_at видны в UI), отдельный changelog-след избыточен.
    """
    from apps.changelog.registry import TRACKED
    assert 'teachers.TeacherComment' not in TRACKED
    assert not hasattr(TeacherComment, 'pgh_event_model')
