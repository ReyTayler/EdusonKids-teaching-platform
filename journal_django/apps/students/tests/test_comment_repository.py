"""Тесты StudentsRepository — функции комментариев."""
from __future__ import annotations

import uuid

import pytest
from django.db import connection

from apps.students import repository


def _create_student() -> int:
    # full_name уникален (UNIQUE constraint) — генерируем уникальное имя на каждого ученика.
    name = f'__test_repo_comment_student__{uuid.uuid4().hex[:8]}'
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES (%s, NOW()) RETURNING id",
            [name],
        )
        return cur.fetchone()[0]


def _cleanup(student_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM student_comment WHERE student_id = %s', [student_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.mark.django_db
def test_add_comment_creates_row():
    student_id = _create_student()
    try:
        comment = repository.add_comment(student_id, 'Текст', author_id=None)
        assert comment.id is not None
        assert comment.student_id == student_id
        assert comment.body == 'Текст'
        assert comment.author_id is None
    finally:
        _cleanup(student_id)


@pytest.mark.django_db
def test_delete_comment_removes_row_and_reports_missing():
    student_id = _create_student()
    try:
        comment = repository.add_comment(student_id, 'Текст', author_id=None)
        assert repository.delete_comment(student_id, comment.id) is True
        assert repository.delete_comment(student_id, comment.id) is False
    finally:
        _cleanup(student_id)


@pytest.mark.django_db
def test_delete_comment_scoped_to_student():
    """Комментарий другого ученика delete_comment не трогает (student_id обязателен в WHERE)."""
    student_a = _create_student()
    student_b = _create_student()
    try:
        comment = repository.add_comment(student_a, 'Текст', author_id=None)
        assert repository.delete_comment(student_b, comment.id) is False
    finally:
        _cleanup(student_a)
        _cleanup(student_b)
