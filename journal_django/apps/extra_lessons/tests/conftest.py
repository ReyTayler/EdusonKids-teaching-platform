"""conftest.py для тестов extra_lessons — фикстуры-самосев в eduson_test."""
from __future__ import annotations

import pytest
from django.db import connection


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def teacher_fixture():
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__el_test_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
    yield teacher_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.fixture
def other_teacher_fixture():
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__el_test_teacher2__') RETURNING id")
        teacher_id = cur.fetchone()[0]
    yield teacher_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.fixture
def direction_fixture():
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO directions (name, total_lessons, active)
            VALUES ('__el_test_dir__', 8, true)
            RETURNING id
            """,
        )
        direction_id = cur.fetchone()[0]
    yield direction_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


@pytest.fixture
def student_fixture():
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO students (full_name)
            VALUES ('__el_test_student__') RETURNING id
            """,
        )
        student_id = cur.fetchone()[0]
    yield student_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.fixture
def group_fixture(direction_fixture, teacher_fixture):
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__el_test_group__', %s, %s, false, 60, true, 0) RETURNING id
            """,
            [direction_fixture, teacher_fixture],
        )
        group_id = cur.fetchone()[0]
    yield group_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.fixture
def membership_fixture(group_fixture, student_fixture, direction_fixture):
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true) RETURNING id
            """,
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count,
                                   unit_price, total_amount, paid_at, created_by)
            VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test') RETURNING id
            """,
            [student_fixture, direction_fixture],
        )
        payment_id = cur.fetchone()[0]
    yield membership_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])
        cur.execute('DELETE FROM payments WHERE id = %s', [payment_id])


@pytest.fixture
def missed_lesson_fixture(group_fixture, teacher_fixture, student_fixture, membership_fixture, present_classmate):
    """Уже проведённый урок группы, на котором student_fixture отсутствовал."""
    from apps.lessons import services as lessons_services

    result = lessons_services.create_lesson_full({
        'lesson_date': '2026-04-01',
        'group_id': group_fixture,
        'teacher_id': teacher_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': present_classmate(
            [{'student_id': student_fixture, 'present': False}], group_fixture),
    })
    lesson_id = result['lesson_id']
    yield lesson_id
    with connection.cursor() as cur:
        # Резолюции пропусков за этот урок (absence_resolutions.missed_lesson —
        # реальный DB-level FK) снести первыми, иначе DELETE FROM lessons ниже
        # упадёт по внешнему ключу.
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


@pytest.fixture
def unpaid_student_fixture():
    """Ученик БЕЗ оплаты (balance=0) — для тестов UnpaidAttendanceBlocked
    в extra_lessons (запрет создавать/проводить доп.урок без баланса)."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO students (full_name)
            VALUES ('__el_test_unpaid_student__') RETURNING id
            """,
        )
        student_id = cur.fetchone()[0]
    yield student_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.fixture
def unpaid_membership_fixture(group_fixture, unpaid_student_fixture):
    """Как membership_fixture, но БЕЗ оплаты — членство есть, payments нет."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true) RETURNING id
            """,
            [group_fixture, unpaid_student_fixture],
        )
        membership_id = cur.fetchone()[0]
    yield membership_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


@pytest.fixture
def missed_lesson_unpaid_fixture(
    group_fixture, teacher_fixture, unpaid_student_fixture, unpaid_membership_fixture,
    present_classmate,
):
    """Уже проведённый урок группы, на котором unpaid_student_fixture (без
    оплаты) отсутствовал — источник для тестов UnpaidAttendanceBlocked."""
    from apps.lessons import services as lessons_services

    result = lessons_services.create_lesson_full({
        'lesson_date': '2026-04-02',
        'group_id': group_fixture,
        'teacher_id': teacher_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': present_classmate(
            [{'student_id': unpaid_student_fixture, 'present': False}], group_fixture),
    })
    lesson_id = result['lesson_id']
    yield lesson_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


def _lessons_done(group_id: int, student_id: int):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT lessons_done FROM group_memberships WHERE group_id = %s AND student_id = %s',
            [group_id, student_id],
        )
        row = cur.fetchone()
    return row[0] if row else None


@pytest.fixture
def lessons_done():
    return _lessons_done
