"""
История «Мои уроки» (GET /api/lessons) — поле students и совпадение с зарплатой.

Спека: docs/superpowers/specs/2026-09-16-teacher-my-lessons-history-design.md.
Фикстуры teacher_fixture/account_fixture — из conftest этого пакета.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.teacher_spa.tests.conftest import _jwt_client

pytestmark = pytest.mark.django_db

MONTH = {'from': '2026-02-01', 'to': '2026-02-28', 'page_size': 500}


@pytest.fixture
def graph(teacher_fixture):
    """Направление, группа преподавателя, пять учеников; чистится в teardown."""
    teacher_id = teacher_fixture[0]
    created = {'teacher_id': teacher_id, 'lessons': [], 'students': [], 'teachers': []}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__mlh_dir__', 16, true) RETURNING id")
        created['direction_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__mlh_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [created['direction_id'], teacher_id])
        created['group_id'] = cur.fetchone()[0]
        for name in ('__mlh_Дарья__', '__mlh_Анна__', '__mlh_Борис__', '__mlh_Вера__', '__mlh_Глеб__'):
            cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id', [name])
            created['students'].append(cur.fetchone()[0])
    yield created
    with connection.cursor() as cur:
        for lid in created['lessons']:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        for sid in created['students']:
            cur.execute('DELETE FROM students WHERE id = %s', [sid])
        cur.execute('DELETE FROM groups WHERE id = %s', [created['group_id']])
        for tid in created['teachers']:
            cur.execute('DELETE FROM teachers WHERE id = %s', [tid])
        cur.execute('DELETE FROM directions WHERE id = %s', [created['direction_id']])


def _lesson(graph, *, date='2026-02-10', number=1, lesson_type='regular',
            attendance=(), teacher_id=None, with_payroll=True):
    """
    Урок с посещениями. attendance — кортежи (student_id, present, is_free, unpaid_skip).
    with_payroll — строка зарплаты, как у всех уроков на проде (замер 2026-09-16).
    """
    tid = teacher_id or graph['teacher_id']
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, %s, %s, 60, %s, 't') RETURNING id",
            [graph['group_id'], tid, date, number, lesson_type])
        lid = cur.fetchone()[0]
        graph['lessons'].append(lid)
        for sid, present, is_free, skip in attendance:
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free, unpaid_skip) '
                'VALUES (%s, %s, %s, %s, %s)', [lid, sid, present, is_free, skip])
        if with_payroll:
            present_count = sum(1 for _, p, f, s in attendance if p and not f and not s)
            cur.execute(
                'INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, payment, penalty) '
                'VALUES (%s, %s, %s, %s, 0, 0)', [lid, tid, len(attendance), present_count])
    return lid


def _get(account_id, params):
    resp = _jwt_client(account_id).get('/api/lessons', params)
    assert resp.status_code == 200, resp.content
    return resp.json()


def _row(body, lesson_id):
    return next(r for r in body['rows'] if r['id'] == lesson_id)


def test_student_statuses_for_all_cases(graph, account_fixture):
    # Ученики вставлены в фикстуре в этом порядке — распаковываем так же, чтобы
    # имена переменных совпадали с реальными именами учеников.
    darya, anna, boris, vera, gleb = graph['students']
    lid = _lesson(graph, attendance=[
        (anna, True, False, False),    # был
        (boris, True, True, False),    # был бесплатно
        (vera, False, False, False),   # не был
        (gleb, False, False, True),    # не посещает
    ])
    burned = _lesson(graph, number=2, lesson_type='burned', date='2026-02-11',
                     attendance=[(darya, True, False, False)])

    body = _get(account_fixture, MONTH)
    statuses = {s['id']: s['status'] for s in _row(body, lid)['students']}
    assert statuses == {anna: 'present', boris: 'free', vera: 'absent', gleb: 'skip'}
    # Сгорание: в базе present=true, но занятия не было — тип урока старше флагов.
    assert [s['status'] for s in _row(body, burned)['students']] == ['burned']


def test_students_sorted_by_name_and_none_lost(graph, account_fixture):
    """
    Все ученики урока на месте и по алфавиту. У lesson_attendance PK в ORM —
    lesson_id, поэтому prefetch мог бы схлопнуть учеников одного урока в одного.
    """
    ids = graph['students']
    lid = _lesson(graph, attendance=[(sid, True, False, False) for sid in ids])

    names = [s['name'] for s in _row(_get(account_fixture, MONTH), lid)['students']]
    assert len(names) == len(ids)
    assert names == sorted(names)


def test_foreign_teacher_lessons_are_hidden(graph, account_fixture):
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__mlh_other__', true) RETURNING id")
        other = cur.fetchone()[0]
    graph['teachers'].append(other)
    mine = _lesson(graph, number=1)
    foreign = _lesson(graph, number=2, teacher_id=other)

    ids = {r['id'] for r in _get(account_fixture, MONTH)['rows']}
    assert mine in ids
    assert foreign not in ids


def test_month_range_and_order(graph, account_fixture):
    early = _lesson(graph, number=1, date='2026-02-03')
    late = _lesson(graph, number=2, date='2026-02-20')
    _lesson(graph, number=3, date='2026-03-02')   # вне месяца

    rows = [r['id'] for r in _get(account_fixture, MONTH)['rows'] if r['id'] in graph['lessons']]
    assert rows == [late, early]


def test_month_matches_payroll(graph, account_fixture):
    """Список уроков месяца совпадает со списком «Зарплаты» строка в строку."""
    from apps.payroll.services import my_payroll_month

    _lesson(graph, number=1, date='2026-02-03', attendance=[(graph['students'][0], True, False, False)])
    _lesson(graph, number=2, date='2026-02-20', lesson_type='burned',
            attendance=[(graph['students'][1], True, False, False)])
    _lesson(graph, number=3, date='2026-02-21', lesson_type='extra',
            attendance=[(graph['students'][2], True, False, False)])

    history = [r['id'] for r in _get(account_fixture, MONTH)['rows']]
    payroll = [r['lessonId'] for r in my_payroll_month(graph['teacher_id'], '2026-02')['rows']]
    assert history == payroll


def test_query_count_does_not_grow_with_lessons(graph, account_fixture):
    client = _jwt_client(account_fixture)
    _lesson(graph, number=1, attendance=[(graph['students'][0], True, False, False)])
    with CaptureQueriesContext(connection) as one:
        client.get('/api/lessons', MONTH)

    for n in (2, 3, 4):
        _lesson(graph, number=n, attendance=[(sid, True, False, False) for sid in graph['students']])
    with CaptureQueriesContext(connection) as many:
        client.get('/api/lessons', MONTH)

    assert len(many.captured_queries) == len(one.captured_queries)
