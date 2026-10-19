"""
conftest.py для тестов teachers.

Фаза 4: HMAC _make_cookie удалён. Аутентификация — JWT через корневые фикстуры
(admin_client, manager_client, teacher_client из корневого conftest.py).
managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope='session')
def django_db_setup():
    """No-op: таблицы managed=False, управляем ими вручную в eduson_test."""
    pass


@pytest.fixture
def stats_teacher():
    """Преподаватель под тесты статистики. Чистится DELETE — eduson_test общая."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__stats_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
    yield teacher_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE teacher_id = %s', [teacher_id])
        cur.execute('DELETE FROM groups WHERE teacher_id = %s', [teacher_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.fixture
def stats_direction():
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, color, active) "
            "VALUES ('__stats_dir__', 36, '#f0b429', true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
    yield direction_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


@pytest.fixture
def make_group(stats_teacher, stats_direction):
    """Фабрика групп преподавателя. Удаление — в teardown stats_teacher."""
    from django.db import connection
    created = []

    def _make(name: str, duration: int = 90, active: bool = True,
              lessons_total=None, direction_id: int | None = None):
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO groups (name, direction_id, teacher_id, is_individual, '
                'lesson_duration_minutes, active, lesson_number_offset, lessons_total) '
                'VALUES (%s, %s, %s, false, %s, %s, 0, %s) RETURNING id',
                [name, direction_id or stats_direction, stats_teacher,
                 duration, active, lessons_total],
            )
            group_id = cur.fetchone()[0]
        created.append(group_id)
        return group_id

    yield _make
    with connection.cursor() as cur:
        for group_id in created:
            cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.fixture
def make_lesson(stats_teacher):
    """Фабрика уроков. `teacher_id` по умолчанию — stats_teacher."""
    from django.db import connection
    counter = {'n': 0}

    def _make(group_id: int, date: str, duration: int = 90,
              lesson_type: str = 'regular', original_teacher_id=None,
              teacher_id: int | None = None):
        counter['n'] += 1
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO lessons (group_id, teacher_id, original_teacher_id, '
                'lesson_date, lesson_number, lesson_duration_minutes, lesson_type, '
                'submitted_at, submitted_by_token) '
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s) RETURNING id",
                [group_id, teacher_id or stats_teacher, original_teacher_id,
                 date, counter['n'], duration, lesson_type,
                 f'__stats_tok_{counter["n"]}__'],
            )
            return cur.fetchone()[0]

    return _make
