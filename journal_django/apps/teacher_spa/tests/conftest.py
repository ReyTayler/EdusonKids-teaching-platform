"""
conftest.py для тестов teacher_spa.

Фаза 4: HMAC _make_cookie / TEST_SECRET удалены. Аутентификация — JWT.
account_fixture исправлен: password (не password_hash), is_active (не active).
managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture(scope='session')
def django_db_setup():
    pass


# ---------------------------------------------------------------------------
# JWT helper
# ---------------------------------------------------------------------------

def _jwt_client(account_id: int) -> APIClient:
    """APIClient с JWT access-cookie для teacher-аккаунта."""
    from apps.accounts.models import Account
    user = Account.objects.get(pk=account_id)
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version
    client = APIClient()
    cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    client.cookies[cookie_name] = str(refresh.access_token)
    return client


# ---------------------------------------------------------------------------
# Граф фикстур (teacher + account + direction + group + student + membership)
# ---------------------------------------------------------------------------

@pytest.fixture
def teacher_fixture():
    """Создаёт тестового препода, возвращает (teacher_id, teacher_name)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name, active) VALUES ('__spa_test_teacher__', true) RETURNING id",
        )
        teacher_id = cur.fetchone()[0]
    yield teacher_id, '__spa_test_teacher__'
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.fixture
def account_fixture(teacher_fixture):
    """
    Создаёт account с role='teacher', привязанный к teacher_fixture.
    Использует AbstractUser-колонки: password, is_active.
    Возвращает account_id.
    """
    teacher_id, _ = teacher_fixture
    pw = make_password('testpass123')
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, is_superuser, first_name, last_name, token_version, date_joined)
            VALUES ('__spa_test__@test.local', %s, 'teacher', %s, true, false, false, '', '', 0, NOW())
            RETURNING id
            """,
            [pw, teacher_id],
        )
        account_id = cur.fetchone()[0]
    yield account_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM accounts WHERE id = %s', [account_id])


@pytest.fixture
def sub_teacher_fixture():
    """Второй препод (заменщик), возвращает (teacher_id, teacher_name)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name, active) VALUES ('__spa_sub_teacher__', true) RETURNING id",
        )
        teacher_id = cur.fetchone()[0]
    yield teacher_id, '__spa_sub_teacher__'
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.fixture
def sub_account_fixture(sub_teacher_fixture):
    """Account препода-заменщика. Возвращает account_id."""
    teacher_id, _ = sub_teacher_fixture
    pw = make_password('testpass123')
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, is_superuser, first_name, last_name, token_version, date_joined)
            VALUES ('__spa_sub__@test.local', %s, 'teacher', %s, true, false, false, '', '', 0, NOW())
            RETURNING id
            """,
            [pw, teacher_id],
        )
        account_id = cur.fetchone()[0]
    yield account_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM accounts WHERE id = %s', [account_id])


@pytest.fixture
def direction_fixture():
    """Создаёт тестовое направление."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO directions (name, total_lessons, active)
            VALUES ('__spa_test_dir__', 8, true)
            RETURNING id
            """,
        )
        direction_id = cur.fetchone()[0]
    yield direction_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


@pytest.fixture
def group_fixture(teacher_fixture, direction_fixture):
    """Создаёт тестовую группу (обычная продолжительность 60 мин)."""
    teacher_id, _ = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__spa_test_group__ пн 10:00', %s, %s, false, 60, true, 0)
            RETURNING id
            """,
            [direction_fixture, teacher_id],
        )
        group_id = cur.fetchone()[0]
    yield group_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.fixture
def half_group_fixture(teacher_fixture, direction_fixture):
    """Создаёт тестовую группу 45 минут (half-lesson)."""
    teacher_id, _ = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__spa_half_group__ 45 минут вт 11:00', %s, %s, false, 45, true, 0)
            RETURNING id
            """,
            [direction_fixture, teacher_id],
        )
        group_id = cur.fetchone()[0]
    yield group_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.fixture
def student_fixture():
    """Создаёт тестового ученика."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO students (full_name)
            VALUES ('__spa_test_student__')
            RETURNING id
            """,
        )
        student_id = cur.fetchone()[0]
    yield student_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.fixture
def membership_fixture(group_fixture, student_fixture, direction_fixture):
    """
    Создаёт membership для group_fixture + student_fixture, с оплатой на 8 уроков
    (remaining=8) — иначе submitLesson блокирует present:true (нет оплаченных уроков).
    """
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
        cur.execute(
            """
            INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count,
                                   unit_price, total_amount, paid_at, created_by)
            VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')
            RETURNING id
            """,
            [student_fixture, direction_fixture],
        )
        payment_id = cur.fetchone()[0]
    yield membership_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])
        cur.execute('DELETE FROM payments WHERE id = %s', [payment_id])


@pytest.fixture
def half_membership_fixture(half_group_fixture, student_fixture, direction_fixture):
    """Membership для half_group_fixture, с оплатой на 8 уроков (remaining=8)."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true)
            RETURNING id
            """,
            [half_group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count,
                                   unit_price, total_amount, paid_at, created_by)
            VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')
            RETURNING id
            """,
            [student_fixture, direction_fixture],
        )
        payment_id = cur.fetchone()[0]
    yield membership_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])
        cur.execute('DELETE FROM payments WHERE id = %s', [payment_id])


# ---------------------------------------------------------------------------
# Хелпер для чтения lessons_done
# ---------------------------------------------------------------------------

def _lessons_done(group_id: int, student_id: int):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT lessons_done FROM group_memberships WHERE group_id = %s AND student_id = %s',
            [group_id, student_id],
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


@pytest.fixture
def lessons_done():
    return _lessons_done


@pytest.fixture
def group_with_two_slots(group_fixture):
    """
    Группа с ДВУМЯ курсовыми позициями на одну дату (мультислот, «два занятия
    подряд»). Возвращает (group_id, date, [position_id, position_id]).
    """
    date = '2026-08-16'
    ids = []
    with connection.cursor() as cur:
        # scheduled_time — NOT NULL без умолчания в схеме, пропустить нельзя.
        for seq, number, at in ((31, 31, '14:00'), (32, 32, '15:30')):
            cur.execute(
                """
                INSERT INTO planned_lessons
                    (group_id, seq, lesson_number, scheduled_date, scheduled_time,
                     status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'pending', NOW(), NOW())
                RETURNING id
                """,
                [group_fixture, seq, number, date, at],
            )
            ids.append(cur.fetchone()[0])
    yield group_fixture, date, ids
    with connection.cursor() as cur:
        cur.execute('DELETE FROM planned_lessons WHERE id = ANY(%s)', [ids])
