"""
conftest.py для тестов payments.

Фаза 4: HMAC _make_cookie удалён. Аутентификация — JWT через корневые фикстуры.
Фикстуры direction/student/group/membership сохранены без изменений.
managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import pytest
from django.db import connection


@pytest.fixture(scope='session')
def django_db_setup():
    pass


# ---------------------------------------------------------------------------
# DB helper — получить первого teacher из БД
# ---------------------------------------------------------------------------

def _get_teacher_id() -> int:
    """Преподаватель для уроков; на пустой eduson_test создаём своего.

    Раньше здесь был pytest.skip — на чистой тестовой БД это молча выключало
    тесты оплат целиком."""
    with connection.cursor() as cur:
        cur.execute('SELECT id FROM teachers LIMIT 1')
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__pay_teacher__') RETURNING id")
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def direction_fixture():
    """Direction с total_lessons=8 → cap_subscriptions=2."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO directions (name, total_lessons, active)
            VALUES ('__pay_test_dir__', 8, true)
            RETURNING id
            """,
        )
        direction_id = cur.fetchone()[0]

    yield direction_id

    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


@pytest.fixture
def student_fixture():
    """Тестовый ученик."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO students (full_name)
            VALUES ('__pay_test_student__')
            RETURNING id
            """,
        )
        student_id = cur.fetchone()[0]

    yield student_id

    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.fixture
def teacher_id_fixture():
    """ID первого учителя из БД."""
    return _get_teacher_id()


@pytest.fixture
def group_fixture(direction_fixture, teacher_id_fixture):
    """Тестовая группа в direction_fixture."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__pay_test_group__', %s, %s, false, 60, true, 0)
            RETURNING id
            """,
            [direction_fixture, teacher_id_fixture],
        )
        group_id = cur.fetchone()[0]

    yield group_id

    with connection.cursor() as cur:
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.fixture
def membership_fixture(group_fixture, student_fixture):
    """Участие ученика в группе."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true)
            RETURNING id
            """,
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]

    yield membership_id

    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


@pytest.fixture
def lesson_60_fixture(group_fixture, teacher_id_fixture):
    """Урок 60 мин (весит 1 урок в балансе)."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lessons
               (group_id, teacher_id, lesson_date, lesson_number,
                lesson_duration_minutes, lesson_type, submitted_by_token)
            VALUES (%s, %s, '2026-01-10', 1, 60, 'group', 'test-token')
            RETURNING id
            """,
            [group_fixture, teacher_id_fixture],
        )
        lesson_id = cur.fetchone()[0]

    yield lesson_id

    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


@pytest.fixture
def lesson_45_fixture(group_fixture, teacher_id_fixture):
    """Урок 45 мин (весит 0.5 урока в балансе)."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lessons
               (group_id, teacher_id, lesson_date, lesson_number,
                lesson_duration_minutes, lesson_type, submitted_by_token)
            VALUES (%s, %s, '2026-01-11', 2, 45, 'group', 'test-token')
            RETURNING id
            """,
            [group_fixture, teacher_id_fixture],
        )
        lesson_id = cur.fetchone()[0]

    yield lesson_id

    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


@pytest.fixture
def attendance_60_fixture(lesson_60_fixture, student_fixture):
    """Посещение урока 60 мин (present=true)."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
            [lesson_60_fixture, student_fixture],
        )

    yield (lesson_60_fixture, student_fixture)

    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
            [lesson_60_fixture, student_fixture],
        )


@pytest.fixture
def attendance_45_fixture(lesson_45_fixture, student_fixture):
    """Посещение урока 45 мин (present=true)."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
            [lesson_45_fixture, student_fixture],
        )

    yield (lesson_45_fixture, student_fixture)

    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
            [lesson_45_fixture, student_fixture],
        )


@pytest.fixture
def payment_fixture(direction_fixture, student_fixture):
    """Одна тестовая оплата: subscriptions_count=1, unit_price=1000, total=4000."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payments
               (student_id, direction_id, subscriptions_count, lessons_count, unit_price, total_amount,
                paid_at, created_by)
            VALUES (%s, %s, 1, 4, 1000.00, 1000.00, '2026-01-01', 'test')
            RETURNING id
            """,
            [student_fixture, direction_fixture],
        )
        payment_id = cur.fetchone()[0]

    yield payment_id

    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE id = %s', [payment_id])
