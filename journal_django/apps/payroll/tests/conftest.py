"""
conftest для тестов payroll.

Фаза 4: HMAC _make_cookie / session_cookie удалены. Аутентификация — JWT через
корневые фикстуры (admin_client, manager_client).
managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import pytest
from django.db import connection


@pytest.fixture(scope='session')
def django_db_setup():
    pass


def _get_teacher_id() -> int:
    with connection.cursor() as cur:
        cur.execute('SELECT id FROM teachers LIMIT 1')
        row = cur.fetchone()
    if not row:
        pytest.skip('No teachers in DB')
    return row[0]


@pytest.fixture
def teacher_id_fixture():
    """Выделенный тестовый учитель — изоляция агрегатов summary от продовых данных."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name) VALUES ('__pr_teacher__') RETURNING id"
        )
        tid = cur.fetchone()[0]
    yield tid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [tid])


@pytest.fixture
def direction_fixture():
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__pr_dir__', 16, true) RETURNING id"
        )
        did = cur.fetchone()[0]
    yield did
    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [did])


@pytest.fixture
def group_fixture(direction_fixture, teacher_id_fixture):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) VALUES ('__pr_group__', %s, %s, false, 60, true, 0) "
            "RETURNING id",
            [direction_fixture, teacher_id_fixture],
        )
        gid = cur.fetchone()[0]
    yield gid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.fixture
def student_fixture():
    """Ученик для строк lesson_attendance (исключения из headcount зарплаты)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name) VALUES ('__pr_student__') RETURNING id"
        )
        student_id = cur.fetchone()[0]
    yield student_id
    with connection.cursor() as cur:
        # Свои строки посещаемости убираем сами: порядок разрушения фикстур
        # не гарантирует, что урок уже удалён, а FK этого не простит.
        cur.execute('DELETE FROM lesson_attendance WHERE student_id = %s', [student_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.fixture
def payroll_fixture(group_fixture, teacher_id_fixture):
    """Один урок + строка payroll. Возвращает (payroll_id, lesson_id)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-04-10', 1, 60, 'regular', 'test') RETURNING id",
            [group_fixture, teacher_id_fixture],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, payment, penalty) "
            "VALUES (%s, %s, 5, 4, 650.00, 0) RETURNING id",
            [lesson_id, teacher_id_fixture],
        )
        payroll_id = cur.fetchone()[0]
    yield payroll_id, lesson_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE id = %s', [payroll_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
