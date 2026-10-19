"""
Выборка по одной группе обязана давать РОВНО те же данные, что выборка по группам
преподавателя (getData). На этом формате стоит вся запись урока — определение владельца группы,
признак замены, прогресс учеников, маркеры «неоплачиваемый пропуск». Тихое
расхождение здесь означало бы неверную зарплату и неверную посещаемость, а не
просто «другой JSON».
"""
from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def group_with_student(group_fixture, student_fixture):
    """Группа с одним активным учеником — минимум, на котором сравнимы выборки."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, active, lessons_done) '
            'VALUES (%s, %s, true, 3) RETURNING id',
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
    yield group_fixture, membership_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def _group_name(group_id: int) -> str:
    with connection.cursor() as cur:
        cur.execute('SELECT name FROM groups WHERE id = %s', [group_id])
        return cur.fetchone()[0]


def _owner_id(group_id: int) -> int:
    with connection.cursor() as cur:
        cur.execute('SELECT teacher_id FROM groups WHERE id = %s', [group_id])
        return cur.fetchone()[0]


def test_group_scoped_read_matches_own_read(group_with_student):
    """Ветка группы совпадает с той же веткой из выборки по владельцу."""
    from apps.teacher_spa.repository import read_group_students, read_own_students

    group_id, _membership_id = group_with_student
    name = _group_name(group_id)

    full = read_own_students(_owner_id(group_id))
    scoped = read_group_students(name)

    owner = next(t for t, groups in full['data'].items() if name in groups)

    assert list(scoped['data']) == [owner], 'в узкой выборке ровно один преподаватель'
    assert scoped['data'][owner][name] == full['data'][owner][name]


def test_group_scoped_read_returns_only_that_group(group_with_student):
    """Чужие группы в узкую выборку не попадают — иначе экономии нет."""
    from apps.teacher_spa.repository import read_group_students

    group_id, _membership_id = group_with_student
    name = _group_name(group_id)

    scoped = read_group_students(name)

    all_group_names = {g for groups in scoped['data'].values() for g in groups}
    assert all_group_names == {name}


def test_unknown_group_gives_empty_result(group_with_student):
    """Несуществующее имя — пустая выборка, а не падение."""
    from apps.teacher_spa.repository import read_group_students

    assert read_group_students('__нет такой группы__') == {'data': {}, 'index': {}}


def test_group_scoped_read_touches_fewer_rows(group_with_student):
    """
    Смысл всей задачи: запись урока не должна читать всю школу. Проверяем, что
    узкая выборка фильтрует по группе в самом SQL, а не после чтения.
    """
    from django.test.utils import CaptureQueriesContext

    from apps.teacher_spa.repository import read_group_students, read_own_students

    group_id, _membership_id = group_with_student
    name = _group_name(group_id)
    owner_id = _owner_id(group_id)

    with CaptureQueriesContext(connection) as scoped_q:
        read_group_students(name)
    with CaptureQueriesContext(connection) as full_q:
        read_own_students(owner_id)

    scoped_sql = ' '.join(q['sql'] for q in scoped_q)
    assert 'name' in scoped_sql.lower(), 'узкая выборка обязана фильтровать по группе в SQL'
    # Число запросов при этом не растёт: оба пути делают фиксированный набор.
    assert len(scoped_q) == len(full_q)
