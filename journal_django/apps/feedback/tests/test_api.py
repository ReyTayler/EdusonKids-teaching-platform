"""GET /api/admin/lesson-feedback — права, поля ответа, фильтры, порядок, пагинация."""
from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

pytestmark = pytest.mark.django_db

URL = '/api/admin/lesson-feedback'
NOT_HELD_DAY = datetime.date(2026, 1, 19)


def _at(day: int) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime(2026, 1, day, 18, 0))


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_anonymous_denied(anon_client):
    assert anon_client.get(URL).status_code in (401, 403)


def test_teacher_denied(teacher_client):
    assert teacher_client.get(URL).status_code == 403


@pytest.mark.parametrize('client_fixture', ['admin_client', 'manager_client', 'superadmin_client'])
def test_staff_allowed(request, client_fixture):
    client = request.getfixturevalue(client_fixture)
    assert client.get(URL).status_code == 200


# ---------------------------------------------------------------------------
# Поля ответа
# ---------------------------------------------------------------------------

def test_fields_for_held_lesson(admin_client, school, held):
    fb = held(school['lesson_id'], understanding=7, interest=6, tech_problems=True,
              comment='Камера отваливалась', submitted_at=_at(12))
    body = admin_client.get(URL).json()
    assert body['total'] == 1
    row = body['rows'][0]
    assert row == {
        'id': fb.id,
        'lesson_id': school['lesson_id'],
        'group_id': school['group_id'],
        'group_name': 'Тест Группа 1',
        'lesson_date': '2026-01-12',
        'record_url': None,
        'teacher_id': school['teacher_id'],
        'teacher_name': 'Тест Преподаватель 1',
        'took_place': True,
        'understanding': 7,
        'interest': 6,
        'tech_problems': True,
        'comment': 'Камера отваливалась',
        'submitted_at': row['submitted_at'],
    }
    assert row['submitted_at'].startswith('2026-01-12T18:00:00')


def test_fields_for_lesson_not_held(admin_client, school, not_held):
    not_held(school['group_id'], school['teacher_id'], NOT_HELD_DAY)
    row = admin_client.get(URL).json()['rows'][0]
    assert row['lesson_id'] is None
    assert row['group_id'] == school['group_id']
    assert row['lesson_date'] == '2026-01-19'
    assert row['took_place'] is False
    assert row['understanding'] is None and row['interest'] is None
    assert row['record_url'] is None
    assert row['group_name'] == 'Тест Группа 1'
    assert 'planned_lesson_id' not in row


def test_record_url_comes_from_lesson(admin_client, school, held):
    from apps.lessons.models import Lesson

    Lesson.objects.filter(pk=school['lesson_id']).update(
        record_url='https://video.example.test/rec/42')
    held(school['lesson_id'])
    row = admin_client.get(URL).json()['rows'][0]
    assert row['record_url'] == 'https://video.example.test/rec/42'


# ---------------------------------------------------------------------------
# Фильтры
# ---------------------------------------------------------------------------

def test_group_filter_matches_held_and_not_held(admin_client, school, other_group, held,
                                                not_held):
    mine_held = held(school['lesson_id'])
    mine_not_held = not_held(school['group_id'], school['teacher_id'], NOT_HELD_DAY)
    held(other_group['lesson_id'])
    not_held(other_group['group_id'], school['teacher_id'], NOT_HELD_DAY)

    body = admin_client.get(URL, {'group_id': school['group_id']}).json()
    assert body['total'] == 2
    assert {r['id'] for r in body['rows']} == {mine_held.id, mine_not_held.id}

    other = admin_client.get(URL, {'group_id': other_group['group_id']}).json()
    assert other['total'] == 2
    assert {r['group_id'] for r in other['rows']} == {other_group['group_id']}


def test_teacher_filter(admin_client, school, other_group, held):
    from apps.teachers.models import Teacher

    second = Teacher.objects.create(name='Тест Преподаватель 2', created_at=timezone.now())
    mine = held(other_group['lesson_id'], teacher_id=second.id)
    held(school['lesson_id'])

    body = admin_client.get(URL, {'teacher_id': second.id}).json()
    assert [r['id'] for r in body['rows']] == [mine.id]
    assert body['rows'][0]['teacher_name'] == 'Тест Преподаватель 2'


def test_lesson_filter(admin_client, school, other_group, held):
    target = held(other_group['lesson_id'])
    held(school['lesson_id'])

    body = admin_client.get(URL, {'lesson_id': other_group['lesson_id']}).json()
    assert [r['id'] for r in body['rows']] == [target.id]


def test_period_filter_by_lesson_date_inclusive(admin_client, school, other_group, held,
                                                not_held):
    # Уроки 12.01 и 13.01, отзыв «не состоялся» на 19.01.
    jan12 = held(school['lesson_id'])
    jan13 = held(other_group['lesson_id'])
    jan19 = not_held(school['group_id'], school['teacher_id'], NOT_HELD_DAY)

    def ids(**params):
        return {r['id'] for r in admin_client.get(URL, params).json()['rows']}

    assert ids(date_from='2026-01-13') == {jan13.id, jan19.id}
    assert ids(date_to='2026-01-13') == {jan12.id, jan13.id}
    assert ids(date_from='2026-01-13', date_to='2026-01-13') == {jan13.id}
    assert ids(date_from='2026-01-12', date_to='2026-01-19') == {jan12.id, jan13.id, jan19.id}


def test_period_combines_with_group_filter(admin_client, school, other_group, held, not_held):
    held(school['lesson_id'])
    held(other_group['lesson_id'])
    target = not_held(school['group_id'], school['teacher_id'], NOT_HELD_DAY)
    body = admin_client.get(URL, {'group_id': school['group_id'],
                                  'date_from': '2026-01-13'}).json()
    assert [r['id'] for r in body['rows']] == [target.id]


@pytest.mark.parametrize('param', ['date_from', 'date_to'])
@pytest.mark.parametrize('value', ['abc', '2026-13-01', '12.01.2026'])
def test_garbage_date_rejected(admin_client, param, value):
    resp = admin_client.get(URL, {param: value})
    assert resp.status_code == 400
    assert param in str(resp.json())


def test_inverted_period_rejected(admin_client):
    resp = admin_client.get(URL, {'date_from': '2026-02-01', 'date_to': '2026-01-01'})
    assert resp.status_code == 400
    assert 'date_from' in str(resp.json())


@pytest.mark.parametrize('param', ['group_id', 'teacher_id', 'lesson_id'])
@pytest.mark.parametrize('value', ['abc', '1.5', '-1', '0'])
def test_garbage_filter_rejected(admin_client, param, value):
    resp = admin_client.get(URL, {param: value})
    assert resp.status_code == 400
    assert param in str(resp.json())


# ---------------------------------------------------------------------------
# Порядок и пагинация
# ---------------------------------------------------------------------------

def test_order_newest_first_with_id_tiebreak(admin_client, school, other_group, held, not_held):
    same_time = _at(20)
    a = held(school['lesson_id'], submitted_at=same_time)
    b = held(other_group['lesson_id'], submitted_at=same_time)
    older = not_held(school['group_id'], school['teacher_id'], NOT_HELD_DAY, submitted_at=_at(5))
    ids = [r['id'] for r in admin_client.get(URL).json()['rows']]
    assert ids == [b.id, a.id, older.id]


def test_pagination_envelope(admin_client, school, other_group, held, not_held):
    held(school['lesson_id'], submitted_at=_at(3))
    held(other_group['lesson_id'], submitted_at=_at(2))
    newest = not_held(school['group_id'], school['teacher_id'], NOT_HELD_DAY, submitted_at=_at(4))

    first = admin_client.get(URL, {'page_size': 2}).json()
    assert (first['total'], first['page'], first['page_size']) == (3, 1, 2)
    assert [r['id'] for r in first['rows']][0] == newest.id
    assert len(first['rows']) == 2

    second = admin_client.get(URL, {'page_size': 2, 'page': 2}).json()
    assert len(second['rows']) == 1
    seen = {r['id'] for r in first['rows']} | {r['id'] for r in second['rows']}
    assert len(seen) == 3


def test_list_has_no_n_plus_one(admin_client, school, other_group, held, not_held):
    """Число запросов не растёт с числом строк: преподаватель подтянут JOIN'ом."""
    from apps.teachers.models import Teacher

    held(school['lesson_id'])
    with CaptureQueriesContext(connection) as one:
        admin_client.get(URL)
    second = Teacher.objects.create(name='Тест Преподаватель 2', created_at=timezone.now())
    held(other_group['lesson_id'], teacher_id=second.id)
    not_held(school['group_id'], second.id, NOT_HELD_DAY)
    not_held(other_group['group_id'], school['teacher_id'], NOT_HELD_DAY)
    with CaptureQueriesContext(connection) as many:
        admin_client.get(URL)
    assert len(many) == len(one)
