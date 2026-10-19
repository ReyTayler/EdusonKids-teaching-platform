"""
E2E для GET /api/admin/students/:id/lessons — вкладка «Уроки» карточки ученика.

RBAC: читают все роли админки (manager/admin/superadmin), преподаватель — нет.
"""
from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def graph():
    created = {}
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__sl_dir__', 16, true) RETURNING id")
        created['direction_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__sl_teacher__') RETURNING id")
        created['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__sl_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [created['direction_id'], created['teacher_id']])
        created['group_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__sl_student__') RETURNING id")
        created['student_id'] = cur.fetchone()[0]
    created['lessons'] = []
    yield created
    with connection.cursor() as cur:
        for lid in created['lessons']:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        cur.execute('DELETE FROM students WHERE id = %s', [created['student_id']])
        cur.execute('DELETE FROM groups WHERE id = %s', [created['group_id']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [created['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id = %s', [created['direction_id']])


def _add_lesson(graph, *, number=1, date='2026-02-10', present=True,
                submitted_at='2026-02-10 18:00:00+03'):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token, submitted_at) "
            "VALUES (%s, %s, %s, %s, 60, 'regular', 't', %s) RETURNING id",
            [graph['group_id'], graph['teacher_id'], date, number, submitted_at])
        lid = cur.fetchone()[0]
        graph['lessons'].append(lid)
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) '
            'VALUES (%s, %s, %s)', [lid, graph['student_id'], present])
    return lid


def _url(graph) -> str:
    return f"/api/admin/students/{graph['student_id']}/lessons"


def test_no_cookie_returns_401(anon_client, graph):
    assert anon_client.get(_url(graph)).status_code == 401


def test_teacher_cookie_returns_403(teacher_client, graph):
    assert teacher_client.get(_url(graph)).status_code == 403


def test_manager_cookie_returns_200(manager_client, graph):
    assert manager_client.get(_url(graph)).status_code == 200


def test_admin_cookie_returns_200(admin_client, graph):
    assert admin_client.get(_url(graph)).status_code == 200


def test_unknown_student_returns_404(admin_client):
    resp = admin_client.get('/api/admin/students/999999999/lessons')
    assert resp.status_code == 404


def test_response_shape_and_row_fields(admin_client, graph):
    lid = _add_lesson(graph)

    data = admin_client.get(_url(graph)).json()
    assert set(data) >= {'rows', 'total', 'page', 'page_size'}
    assert data['total'] == 1
    row = data['rows'][0]
    assert row['lesson_id'] == lid
    assert row['kind'] == 'regular'
    assert row['duration_minutes'] == 60
    assert row['lesson_date'] == '2026-02-10'
    assert row['submitted_at'].startswith('2026-02-10')
    assert row['group_name'] == '__sl_group__'
    assert row['teacher_name'] == '__sl_teacher__'
    assert row['direction_name'] == '__sl_dir__'
    assert row['recognized_amount'] == '0.00'
    assert row['is_debt'] is True


def test_absences_are_not_listed(admin_client, graph):
    kept = _add_lesson(graph, number=1)
    _add_lesson(graph, number=2, present=False)

    data = admin_client.get(_url(graph)).json()
    assert [r['lesson_id'] for r in data['rows']] == [kept]


def test_pagination_slices_rows(admin_client, graph):
    _add_lesson(graph, number=1, submitted_at='2026-02-10 18:00:00+03')
    newest = _add_lesson(graph, number=2, submitted_at='2026-02-11 18:00:00+03')

    data = admin_client.get(_url(graph) + '?page=1&page_size=1').json()
    assert data['total'] == 2
    assert data['page_size'] == 1
    assert [r['lesson_id'] for r in data['rows']] == [newest]


def test_sort_by_lesson_date_asc(admin_client, graph):
    """Дата занятия по возрастанию — порядок обратный дефолтному."""
    early = _add_lesson(graph, number=1, date='2026-02-01',
                        submitted_at='2026-03-01 12:00:00+03')
    late = _add_lesson(graph, number=2, date='2026-02-20',
                       submitted_at='2026-02-20 18:00:00+03')

    data = admin_client.get(_url(graph) + '?sort_by=lesson_date&sort_dir=asc').json()
    assert [r['lesson_id'] for r in data['rows']] == [early, late]

    data = admin_client.get(_url(graph) + '?sort_by=lesson_date&sort_dir=desc').json()
    assert [r['lesson_id'] for r in data['rows']] == [late, early]


def test_invalid_sort_by_returns_400(admin_client, graph):
    resp = admin_client.get(_url(graph) + '?sort_by=lesson__group__name')
    assert resp.status_code == 400


def test_invalid_sort_dir_returns_400(admin_client, graph):
    resp = admin_client.get(_url(graph) + '?sort_dir=sideways')
    assert resp.status_code == 400


def test_row_carries_lesson_number(admin_client, graph):
    _add_lesson(graph, number=7)

    row = admin_client.get(_url(graph)).json()['rows'][0]
    assert row['lesson_number'] == '7.0'


def test_period_params_filter_rows(admin_client, graph):
    _add_lesson(graph, number=1, date='2026-01-15')
    inside = _add_lesson(graph, number=2, date='2026-02-10')

    data = admin_client.get(
        _url(graph) + '?date_from=2026-02-01&date_to=2026-02-28').json()
    assert [r['lesson_id'] for r in data['rows']] == [inside]


def test_invalid_period_returns_400(admin_client, graph):
    assert admin_client.get(_url(graph) + '?date_from=вчера').status_code == 400


def test_reversed_period_returns_400(admin_client, graph):
    resp = admin_client.get(_url(graph) + '?date_from=2026-02-28&date_to=2026-02-01')
    assert resp.status_code == 400
