"""
Тёзки в одной группе (Eduson: у учеников записано только имя ребёнка).

Раньше submitLesson сопоставлял ученика по имени словарём {full_name: row}:
два «Миши» в группе схлопывались в одного, и посещение молча записывалось не
тому ребёнку (второй терялся). После снятия UNIQUE с students.full_name это
реальный путь порчи посещаемости и денег, поэтому:
  - getData отдаёт id ученика и skipIds (маркеры пропусков по id);
  - submitLesson принимает id ученика и сопоставляет по нему;
  - без id неоднозначное имя — явный отказ, а не угадывание.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.teacher_spa.tests.test_teacher_spa_api import _client, _get_lesson_id
from apps.teacher_spa.tests.payloads import VALID_FEEDBACK, VALID_RECORD_URL, with_feedback

pytestmark = pytest.mark.django_db

GROUP_NAME = '__spa_test_group__ пн 10:00'
NAME = '__spa_namesake__'


@pytest.fixture
def namesakes(group_fixture, direction_fixture):
    """Два ученика с одинаковым именем в одной группе, у обоих оплачено 8 уроков."""
    ids = []
    with connection.cursor() as cur:
        for _ in range(2):
            cur.execute("INSERT INTO students (full_name) VALUES (%s) RETURNING id", [NAME])
            sid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s, %s, 0, true)",
                [group_fixture, sid],
            )
            cur.execute(
                """
                INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count,
                                       unit_price, total_amount, paid_at, created_by)
                VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')
                """,
                [sid, direction_fixture],
            )
            ids.append(sid)
    yield ids
    # Явная очистка ДО teardown group_fixture/direction_fixture: те удаляют группу
    # и направление прямым DELETE, а pytest-django перед откатом проверяет
    # отложенные FK (check_constraints) — висящие членства/оплаты уронили бы тест.
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lesson_feedback WHERE group_id = %s', [group_fixture])
        cur.execute('DELETE FROM payroll WHERE lesson_id IN '
                    '(SELECT id FROM lessons WHERE group_id = %s)', [group_fixture])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id IN '
                    '(SELECT id FROM lessons WHERE group_id = %s)', [group_fixture])
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_fixture])
        cur.execute('DELETE FROM lesson_skips WHERE student_id = ANY(%s)', [ids])
        cur.execute('DELETE FROM group_memberships WHERE student_id = ANY(%s)', [ids])
        cur.execute('DELETE FROM payments WHERE student_id = ANY(%s)', [ids])
        cur.execute('DELETE FROM students WHERE id = ANY(%s)', [ids])


def _attendance(lesson_id: int) -> dict[int, bool]:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT student_id, present FROM lesson_attendance WHERE lesson_id = %s',
            [lesson_id],
        )
        return dict(cur.fetchall())


def test_get_data_exposes_student_ids(account_fixture, group_fixture, namesakes):
    from apps.lessons.models import LessonSkip
    first, second = namesakes
    LessonSkip.objects.create(group_id=group_fixture, student_id=second, lesson_number=3)

    body = _client('teacher', account_fixture).post('/api/getData', {}, format='json').json()
    # getData отдаёт уже срез своего преподавателя: {группа: ...}.
    grp = body['data'][GROUP_NAME]
    assert sorted(s['id'] for s in grp['students']) == sorted(namesakes)
    assert {s['name'] for s in grp['students']} == {NAME}
    # По имени тёзок не различить, по id — да: помечен только второй.
    assert grp['skips'] == {'3': [NAME]}
    assert grp['skipIds'] == {'3': [second]}


def test_submit_by_id_marks_exact_namesake(teacher_fixture, account_fixture, group_fixture, namesakes):
    first, second = namesakes
    resp = _client('teacher', account_fixture).post('/api/submitLesson', {
        'feedback': VALID_FEEDBACK,
        'recordUrl': VALID_RECORD_URL,
        'group': GROUP_NAME,
        'date': '2026-06-10',
        'students': [
            {'id': second, 'name': NAME, 'present': True},
            {'id': first, 'name': NAME, 'present': False},
        ],
    }, format='json')
    assert resp.status_code == 200
    assert resp.json()['success'] is True, resp.json()

    lesson_id = _get_lesson_id(group_fixture, f'acct:{account_fixture}')
    assert _attendance(lesson_id) == {first: False, second: True}


def test_submit_ambiguous_name_without_id_refused(
    teacher_fixture, account_fixture, group_fixture, namesakes,
):
    resp = _client('teacher', account_fixture).post('/api/submitLesson', {
        'feedback': VALID_FEEDBACK,
        'recordUrl': VALID_RECORD_URL,
        'group': GROUP_NAME,
        'date': '2026-06-10',
        'students': [{'name': NAME, 'present': True}],
    }, format='json')
    assert resp.status_code == 200
    body = resp.json()
    assert body['success'] is False
    assert NAME in body['error']
    assert body['code'] == 'ambiguous_name'
    # Отказ — до записи: урок не создан.
    assert _get_lesson_id(group_fixture, f'acct:{account_fixture}') is None


def _submit(account_id: int, students: list[dict]):
    return _client('teacher', account_id).post('/api/submitLesson', {
        'feedback': VALID_FEEDBACK,
        'recordUrl': VALID_RECORD_URL,
        'group': GROUP_NAME, 'date': '2026-06-10', 'students': students,
    }, format='json')


STALE = 'Список учеников изменился'


def test_submit_removed_student_id_refused(account_fixture, group_fixture, namesakes, student_fixture):
    """Ученика сняли с группы (членство неактивно), пока форма была открыта →
    отказ до записи, а не тихий урок без него."""
    first, _ = namesakes
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, false) RETURNING id",
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
    try:
        resp = _submit(account_fixture, [
            {'id': first, 'name': NAME, 'present': True},
            {'id': student_fixture, 'name': '__spa_test_student__', 'present': True},
        ])
        body = resp.json()
        assert resp.status_code == 200
        assert body['success'] is False
        assert STALE in body['error']
        assert body['code'] == 'roster_stale'
        assert _get_lesson_id(group_fixture, f'acct:{account_fixture}') is None
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def test_submit_student_of_neighbour_group_refused(
    teacher_fixture, account_fixture, group_fixture, direction_fixture, namesakes, student_fixture,
):
    """Ученик реально состоит в СОСЕДНЕЙ группе того же преподавателя — его id в
    чужой форме не даёт записать ему посещение в эту группу."""
    teacher_id, _ = teacher_fixture
    first, _ = namesakes
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__spa_neighbour_group__ ср 12:00', %s, %s, false, 60, true, 0)
            RETURNING id
            """,
            [direction_fixture, teacher_id],
        )
        other_group = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true)",
            [other_group, student_fixture],
        )
    try:
        resp = _submit(account_fixture, [
            {'id': first, 'name': NAME, 'present': True},
            {'id': student_fixture, 'name': '__spa_test_student__', 'present': True},
        ])
        body = resp.json()
        assert body['success'] is False
        assert STALE in body['error']
        assert body['code'] == 'roster_stale'
        assert _get_lesson_id(group_fixture, f'acct:{account_fixture}') is None
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [other_group])
            cur.execute('DELETE FROM groups WHERE id = %s', [other_group])


def test_submit_duplicate_id_400(account_fixture, group_fixture, namesakes):
    """Один ученик дважды в списке — 400 от сериализатора, а не 500 на UNIQUE."""
    first, _ = namesakes
    resp = _submit(account_fixture, [
        {'id': first, 'name': NAME, 'present': True},
        {'id': first, 'name': NAME, 'present': False},
    ])
    assert resp.status_code == 400
    assert 'students' in resp.json()['details']
    assert _get_lesson_id(group_fixture, f'acct:{account_fixture}') is None


def test_submit_mixed_id_and_unique_name_ok(
    # namesakes — после student/membership: его teardown (удаление уроков группы)
    # обязан пройти раньше, чем удаление ученика с посещаемостью.
    account_fixture, group_fixture, student_fixture, membership_fixture, namesakes,
):
    """Тёзки — по id, ученик с уникальным именем — по имени без id: урок пишется."""
    first, second = namesakes
    resp = _submit(account_fixture, [
        {'id': first, 'name': NAME, 'present': True},
        {'id': second, 'name': NAME, 'present': False},
        {'name': '__spa_test_student__', 'present': True},
    ])
    assert resp.json()['success'] is True, resp.json()
    lesson_id = _get_lesson_id(group_fixture, f'acct:{account_fixture}')
    assert _attendance(lesson_id) == {first: True, second: False, student_fixture: True}
