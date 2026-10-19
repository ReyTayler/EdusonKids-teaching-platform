"""
E2E дашборда «Посещения учеников».

  GET /api/admin/reports/attendance-dashboard/summary
  GET /api/admin/reports/attendance-dashboard/students

RBAC: manager/admin/superadmin читают, преподаватель — нет.
"""
from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db

SUMMARY_URL = '/api/admin/reports/attendance-dashboard/summary'
STUDENTS_URL = '/api/admin/reports/attendance-dashboard/students'
PERIOD = 'date_from=2026-02-01&date_to=2026-02-28'


@pytest.fixture
def graph():
    created = {'lessons': [], 'students': []}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__adapi_dir__', 16, true) RETURNING id")
        created['direction_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__adapi_teacher__') RETURNING id")
        created['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__adapi_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [created['direction_id'], created['teacher_id']])
        created['group_id'] = cur.fetchone()[0]
    yield created
    with connection.cursor() as cur:
        for lid in created['lessons']:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        for sid in created['students']:
            cur.execute('DELETE FROM students WHERE id = %s', [sid])
        cur.execute('DELETE FROM groups WHERE id = %s', [created['group_id']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [created['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id = %s', [created['direction_id']])


def _student(graph, name):
    with connection.cursor() as cur:
        cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id', [name])
        sid = cur.fetchone()[0]
    graph['students'].append(sid)
    return sid


def _lesson(graph, sid, *, date='2026-02-10', number=1, duration=60,
            lesson_type='regular', is_free=False):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, %s, %s, %s, %s, 't') RETURNING id",
            [graph['group_id'], graph['teacher_id'], date, number, duration, lesson_type])
        lid = cur.fetchone()[0]
        graph['lessons'].append(lid)
        cur.execute('INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
                    'VALUES (%s, %s, true, %s)', [lid, sid, is_free])
    return lid


def test_summary_requires_auth(anon_client):
    assert anon_client.get(f'{SUMMARY_URL}?{PERIOD}').status_code == 401


def test_summary_forbidden_for_teacher(teacher_client):
    assert teacher_client.get(f'{SUMMARY_URL}?{PERIOD}').status_code == 403


def test_students_requires_auth(anon_client):
    assert anon_client.get(f'{STUDENTS_URL}?{PERIOD}').status_code == 401


def test_students_forbidden_for_teacher(teacher_client):
    assert teacher_client.get(f'{STUDENTS_URL}?{PERIOD}').status_code == 403


def test_manager_can_read_both(manager_client):
    assert manager_client.get(f'{SUMMARY_URL}?{PERIOD}').status_code == 200
    assert manager_client.get(f'{STUDENTS_URL}?{PERIOD}').status_code == 200


def test_summary_shape(admin_client, graph):
    sid = _student(graph, '__adapi_s1__')
    _lesson(graph, sid, number=1)

    data = admin_client.get(f'{SUMMARY_URL}?{PERIOD}').json()
    assert set(data) == {'all_time_lessons', 'period_lessons'}
    assert float(data['period_lessons']) >= 1


def test_students_row_shape_and_envelope(admin_client, graph):
    sid = _student(graph, '__adapi_s2__')
    _lesson(graph, sid, number=1, duration=45)
    _lesson(graph, sid, number=2, lesson_type='extra')
    _lesson(graph, sid, number=3, is_free=True)

    data = admin_client.get(f'{STUDENTS_URL}?{PERIOD}&filter[full_name]=__adapi_s2__').json()
    assert set(data) >= {'rows', 'total', 'page', 'page_size'}
    assert data['total'] == 1
    row = data['rows'][0]
    assert row['student_id'] == sid
    assert row['full_name'] == '__adapi_s2__'
    assert float(row['regular']) == 0.5
    assert float(row['extra']) == 1
    assert float(row['burned']) == 0
    assert float(row['free']) == 1
    assert float(row['billed']) == 1.5


def test_students_name_filter_narrows(admin_client, graph):
    a = _student(graph, '__adapi_Иванов__')
    _student(graph, '__adapi_Петров__')
    _lesson(graph, a, number=1)

    data = admin_client.get(f'{STUDENTS_URL}?{PERIOD}&filter[full_name]=иванов').json()
    assert [r['student_id'] for r in data['rows']] == [a]


def test_missing_period_returns_400(admin_client):
    assert admin_client.get(STUDENTS_URL).status_code == 400
    assert admin_client.get(SUMMARY_URL).status_code == 400


def test_invalid_period_returns_400(admin_client):
    assert admin_client.get(f'{SUMMARY_URL}?date_from=вчера&date_to=2026-02-28').status_code == 400


def test_reversed_period_returns_400(admin_client):
    """Конец раньше начала — почти наверняка опечатка, молчать о ней нельзя."""
    resp = admin_client.get(f'{SUMMARY_URL}?date_from=2026-02-28&date_to=2026-02-01')
    assert resp.status_code == 400


def test_invalid_sort_returns_400(admin_client):
    assert admin_client.get(f'{STUDENTS_URL}?{PERIOD}&sort_by=student__id').status_code == 400
    assert admin_client.get(f'{STUDENTS_URL}?{PERIOD}&sort_dir=sideways').status_code == 400


def test_pagination_slices(admin_client, graph):
    a = _student(graph, '__adapi_pag_a__')
    b = _student(graph, '__adapi_pag_b__')
    _lesson(graph, a, number=1)
    _lesson(graph, b, number=2)

    data = admin_client.get(
        f'{STUDENTS_URL}?{PERIOD}&filter[full_name]=__adapi_pag_&page_size=1').json()
    assert data['total'] == 2
    assert len(data['rows']) == 1
