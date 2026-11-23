"""Тесты TeachersRepository — функции комментариев."""
from __future__ import annotations

import uuid

import pytest
from django.db import connection

from apps.teachers import repository


def _create_teacher() -> int:
    # name уникален (UNIQUE) — генерируем уникальное имя на каждого преподавателя.
    name = f'__test_repo_comment_teacher__{uuid.uuid4().hex[:8]}'
    with connection.cursor() as cur:
        cur.execute('INSERT INTO teachers (name) VALUES (%s) RETURNING id', [name])
        return cur.fetchone()[0]


def _cleanup(teacher_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teacher_comment WHERE teacher_id = %s', [teacher_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.mark.django_db
def test_add_comment_creates_row():
    teacher_id = _create_teacher()
    try:
        comment = repository.add_comment(teacher_id, 'Текст', author_id=None)
        assert comment.id is not None
        assert comment.teacher_id == teacher_id
        assert comment.body == 'Текст'
        assert comment.author_id is None
        assert comment.created_at is not None
    finally:
        _cleanup(teacher_id)


@pytest.mark.django_db
def test_delete_comment_removes_row_and_reports_missing():
    teacher_id = _create_teacher()
    try:
        comment = repository.add_comment(teacher_id, 'Текст', author_id=None)
        assert repository.delete_comment(teacher_id, comment.id) is True
        assert repository.delete_comment(teacher_id, comment.id) is False
    finally:
        _cleanup(teacher_id)


@pytest.mark.django_db
def test_delete_comment_scoped_to_teacher():
    """Комментарий другого преподавателя delete_comment не трогает."""
    teacher_a = _create_teacher()
    teacher_b = _create_teacher()
    try:
        comment = repository.add_comment(teacher_a, 'Текст', author_id=None)
        assert repository.delete_comment(teacher_b, comment.id) is False
        with connection.cursor() as cur:
            cur.execute('SELECT 1 FROM teacher_comment WHERE id = %s', [comment.id])
            assert cur.fetchone() is not None
    finally:
        _cleanup(teacher_a)
        _cleanup(teacher_b)


@pytest.mark.django_db
def test_teacher_exists():
    teacher_id = _create_teacher()
    try:
        assert repository.teacher_exists(teacher_id) is True
        assert repository.teacher_exists(999999999) is False
    finally:
        _cleanup(teacher_id)
