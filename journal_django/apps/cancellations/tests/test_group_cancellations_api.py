"""Вкладка группы «Отмены уроков»: GET /api/admin/groups/<pk>/cancellations (B6 плана).

Контракт: manager/admin/superadmin, StandardPagination ({rows,total,page,page_size}),
порядок фиксирован — lesson_date DESC, scheduled_time DESC, id DESC. Снимок
compensated не отдаётся никому (В3: колонки компенсации во вкладке нет, а флаг —
часть мотивации). Отмены без записи (маркер до фичи) во вкладке не видны.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

pytestmark = pytest.mark.django_db

D = datetime.date
T = datetime.time


def _url(gid: int) -> str:
    return f'/api/admin/groups/{gid}/cancellations'


@pytest.fixture
def author(db):
    """Учётка автора отмен — внутри транзакции теста, откатывается вместе с ним."""
    from apps.accounts.models import Account

    return Account.objects.create(email='cancel-author@example.com', role='manager',
                                  full_name='Анна Менеджер')


@pytest.fixture
def other_group(school):
    from apps.groups.models import Group

    return Group.objects.create(
        name='Тест Отмены Чужая группа', direction_id=school['direction_id'],
        teacher_id=school['teacher_id'], is_individual=False,
        lesson_duration_minutes=60, lessons_per_week=1, active=True,
        created_at=timezone.now(),
    )


def test_rows_shape_and_fixed_order(manager_client, school, make_record, make_reason, author):
    gid, tid = school['group_id'], school['teacher_id']
    reason = make_reason(side='client', name='Заболел')
    old = make_record(group_id=gid, teacher_id=tid, reason=reason, lesson_date=D(2026, 9, 1),
                      created_by=author)
    # Одна дата, разное время: позже по времени — выше.
    early = make_record(group_id=gid, teacher_id=None, side='teacher', is_other=True,
                        compensated=False, reason_text='Срочная поездка, ' + 'х' * 300,
                        lesson_date=D(2026, 10, 5), scheduled_time=T(9, 0))
    late = make_record(group_id=gid, teacher_id=tid, reason=reason,
                       lesson_date=D(2026, 10, 5), scheduled_time=T(18, 0),
                       notified_on=D(2026, 10, 4))
    # Полный дубль даты и времени: тай-брейк по id DESC.
    late_dup = make_record(group_id=gid, teacher_id=tid, reason=reason,
                           lesson_date=D(2026, 10, 5), scheduled_time=T(18, 0))

    resp = manager_client.get(_url(gid))

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body['total'] == 4 and body['page'] == 1 and body['page_size'] == 50
    assert [r['id'] for r in body['rows']] == [late_dup.id, late.id, early.id, old.id]

    by_id = {r['id']: r for r in body['rows']}
    assert by_id[late.id] == {
        'id': late.id, 'lesson_date': '2026-10-05', 'scheduled_time': '18:00',
        'side': 'client', 'reason_text': 'Заболел', 'is_other': False,
        'notified_on': '2026-10-04', 'teacher_id': tid,
        'teacher_name': 'Тест Преподаватель 1',
        'created_at': by_id[late.id]['created_at'], 'created_by_name': None,
    }
    assert by_id[late.id]['created_at']
    # «Другая причина»: полный текст (вкладка для менеджеров), без преподавателя.
    assert by_id[early.id]['reason_text'] == 'Срочная поездка, ' + 'х' * 300
    assert by_id[early.id]['is_other'] is True
    assert by_id[early.id]['notified_on'] is None
    assert by_id[early.id]['teacher_id'] is None and by_id[early.id]['teacher_name'] is None
    assert by_id[old.id]['created_by_name'] == 'Анна Менеджер'


def test_created_by_name_falls_back_to_email(admin_client, school, make_record, author):
    author.full_name = ''
    author.save(update_fields=['full_name'])
    make_record(group_id=school['group_id'], teacher_id=None, side='teacher', is_other=True,
                compensated=False, reason_text='Другое', lesson_date=D(2026, 9, 1),
                created_by=author)

    rows = admin_client.get(_url(school['group_id'])).json()['rows']

    assert rows[0]['created_by_name'] == 'cancel-author@example.com'


@pytest.mark.parametrize('client_name', ['manager_client', 'admin_client', 'superadmin_client'])
def test_compensated_never_exposed(request, client_name, school, make_record, make_reason):
    client = request.getfixturevalue(client_name)
    reason = make_reason(side='teacher', name='Болезнь', compensation=True)
    make_record(group_id=school['group_id'], teacher_id=school['teacher_id'], side='teacher',
                reason=reason, reason_text='Болезнь', compensated=True,
                lesson_date=D(2026, 9, 1))

    resp = client.get(_url(school['group_id']))

    assert resp.status_code == 200
    text = resp.content.decode().lower()
    assert 'compensat' not in text and 'компенсац' not in text
    assert set(resp.json()['rows'][0]) == {
        'id', 'lesson_date', 'scheduled_time', 'side', 'reason_text', 'is_other',
        'notified_on', 'teacher_id', 'teacher_name', 'created_at', 'created_by_name',
    }


def test_pagination(manager_client, school, make_record, make_reason):
    gid = school['group_id']
    reason = make_reason()
    ids = [make_record(group_id=gid, teacher_id=None, reason=reason,
                       lesson_date=D(2026, 9, d)).id for d in (1, 2, 3)]

    first = manager_client.get(_url(gid), {'page_size': 1}).json()
    second = manager_client.get(_url(gid), {'page_size': 1, 'page': 2}).json()

    assert first['total'] == 3 and first['page_size'] == 1
    assert [r['id'] for r in first['rows']] == [ids[2]]
    assert second['page'] == 2 and [r['id'] for r in second['rows']] == [ids[1]]


def test_only_own_group_and_only_recorded(manager_client, school, other_group, make_record,
                                          make_reason, make_marker):
    gid = school['group_id']
    reason = make_reason()
    mine = make_record(group_id=gid, teacher_id=None, reason=reason, lesson_date=D(2026, 9, 1))
    make_record(group_id=other_group.id, teacher_id=None, reason=reason,
                lesson_date=D(2026, 9, 2))
    # Старая отмена: маркер без записи — во вкладку не попадает.
    make_marker(gid, school['teacher_id'], D(2026, 9, 8))

    body = manager_client.get(_url(gid)).json()

    assert body['total'] == 1 and [r['id'] for r in body['rows']] == [mine.id]


def test_empty_group_returns_empty_page(manager_client, other_group):
    body = manager_client.get(_url(other_group.id)).json()

    assert body == {'rows': [], 'total': 0, 'page': 1, 'page_size': 50}


def test_missing_group_404(manager_client, school):
    from apps.groups.models import Group

    missing = (Group.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1000

    assert manager_client.get(_url(missing)).status_code == 404


def test_teacher_forbidden(teacher_client, school):
    assert teacher_client.get(_url(school['group_id'])).status_code == 403


def test_anonymous_rejected(anon_client, school):
    assert anon_client.get(_url(school['group_id'])).status_code in (401, 403)


def test_read_only(manager_client, school):
    assert manager_client.post(_url(school['group_id']), {}, format='json').status_code == 405


def test_constant_query_count(manager_client, school, make_record, make_reason, author):
    """Имена преподавателя и автора — select_related, а не запрос на строку."""
    from apps.teachers.models import Teacher

    gid = school['group_id']
    reason = make_reason()

    def _add(n: int) -> None:
        for i in range(n):
            teacher = Teacher.objects.create(name=f'Тест Отмены Препод {timezone.now()} {i}',
                                             created_at=timezone.now())
            make_record(group_id=gid, teacher_id=teacher.id, reason=reason,
                        lesson_date=D(2026, 9, 1) + datetime.timedelta(days=i),
                        created_by=author)

    _add(1)
    with CaptureQueriesContext(connection) as one:
        assert manager_client.get(_url(gid)).status_code == 200
    _add(5)
    with CaptureQueriesContext(connection) as many:
        assert len(manager_client.get(_url(gid)).json()['rows']) == 6

    assert len(many) == len(one)
