"""Тесты вкладки «Заполнить»: сервис мёржа + API (RBAC, пагинация, фильтр)."""
from __future__ import annotations

import datetime

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Account
from apps.core.utils.dates import MSK
from apps.dashboard import fill_service


@pytest.fixture
def fill_setup(db):
    """Преподаватель + активная группа С УЧЕНИКОМ + плановые строки под разные кейсы.

    Ученик обязателен: группа без активных членств во вкладку не попадает вовсе
    (см. test_unfilled_lessons_skips_group_without_students), и без него фикстура
    проверяла бы не тот случай.
    """
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__fill_T__', true) RETURNING id")
        teacher = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name,total_lessons,color,active) "
            "VALUES ('__fill_dir__',8,'#4F59F9',true) RETURNING id")
        direction = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
            "lesson_duration_minutes,group_start_date,active,lesson_number_offset) "
            "VALUES ('__fill_G__',%s,%s,false,60,'2026-06-01',true,0) RETURNING id",
            [direction, teacher])
        group = cur.fetchone()[0]
        # A: overdue (прошлое, pending, без факта)  seq=1
        cur.execute(
            "INSERT INTO planned_lessons (group_id,seq,lesson_number,scheduled_date,"
            "scheduled_time,teacher_id,status,created_at,updated_at) "
            "VALUES (%s,1,1,'2026-06-02','10:00',%s,'pending',NOW(),NOW())", [group, teacher])
        # B: сегодня, но время ещё НЕ наступило относительно инжектированного now  seq=2
        cur.execute(
            "INSERT INTO planned_lessons (group_id,seq,lesson_number,scheduled_date,"
            "scheduled_time,teacher_id,status,created_at,updated_at) "
            "VALUES (%s,2,2,'2026-07-01','23:00',%s,'pending',NOW(),NOW())", [group, teacher])
        cur.execute("INSERT INTO students (full_name, created_at) "
                    "VALUES ('__fill_student__', NOW()) RETURNING id")
        student = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s,%s,0,true)', [group, student])
    data = {'teacher': teacher, 'direction': direction, 'group': group, 'student': student}
    yield data
    with connection.cursor() as cur:
        cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group])
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group])
        cur.execute('DELETE FROM groups WHERE id = %s', [group])
        cur.execute('DELETE FROM students WHERE id = %s', [student])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher])


def test_unfilled_lessons_includes_overdue_excludes_future(fill_setup):
    # now = 2026-07-01 12:00 МСК: строка A (02.06) прошла, строка B (01.07 23:00) — нет
    now = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=MSK)
    rows = fill_service.unfilled_lessons(now=now)
    ours = [r for r in rows if r['group_id'] == fill_setup['group']]
    assert len(ours) == 1
    assert ours[0]['date'] == '2026-06-02'
    assert ours[0]['kind'] == 'planned'
    assert ours[0]['time'] == '10:00'
    assert ours[0]['teacher_name'] == '__fill_T__'
    assert ours[0]['lesson_number'] == 1.0


def test_unfilled_lessons_skips_group_without_students(fill_setup):
    """
    Группа, в которой не осталось ни одного активного ученика, из вкладки уходит:
    заполнять там нечего, а её план продолжал бы копить просрочку и маскировать
    настоящие долги преподавателей.
    """
    now = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=MSK)
    before = fill_service.unfilled_lessons(now=now)
    assert any(r['group_id'] == fill_setup['group'] for r in before), 'пока ученик есть — видно'

    with connection.cursor() as cur:
        cur.execute('UPDATE group_memberships SET active = false WHERE group_id = %s',
                    [fill_setup['group']])

    after = fill_service.unfilled_lessons(now=now)
    assert not any(r['group_id'] == fill_setup['group'] for r in after)


def test_unfilled_lessons_teacher_filter(fill_setup):
    now = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=MSK)
    other = fill_service.unfilled_lessons(teacher_id=fill_setup['teacher'] + 100000, now=now)
    assert all(r['group_id'] != fill_setup['group'] for r in other)


def test_unfilled_lessons_sorted_old_first(fill_setup):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO planned_lessons (group_id,seq,lesson_number,scheduled_date,"
            "scheduled_time,teacher_id,status,created_at,updated_at) "
            "VALUES (%s,3,3,'2026-05-01','09:00',%s,'pending',NOW(),NOW())",
            [fill_setup['group'], fill_setup['teacher']])
    now = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=MSK)
    ours = [r for r in fill_service.unfilled_lessons(sort_dir='asc', now=now)
            if r['group_id'] == fill_setup['group']]
    assert [r['date'] for r in ours] == ['2026-05-01', '2026-06-02']  # старые сверху (asc)


def test_unfilled_lessons_sorted_default_is_descending(fill_setup):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO planned_lessons (group_id,seq,lesson_number,scheduled_date,"
            "scheduled_time,teacher_id,status,created_at,updated_at) "
            "VALUES (%s,4,4,'2026-05-01','09:00',%s,'pending',NOW(),NOW())",
            [fill_setup['group'], fill_setup['teacher']])
    now = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=MSK)
    ours = [r for r in fill_service.unfilled_lessons(now=now)  # sort_dir omitted → default
            if r['group_id'] == fill_setup['group']]
    assert [r['date'] for r in ours] == ['2026-06-02', '2026-05-01']  # новые сверху (desc, default)


# ---------------------------------------------------------------------------
# API: RBAC, envelope, teacher_id validation
# ---------------------------------------------------------------------------

URL = '/api/admin/dashboard/unfilled-lessons'

_FILL_ROLE_EMAILS = {
    'admin': '__fill_admin__@example.com',
    'manager': '__fill_manager__@example.com',
    'teacher': '__fill_teacher__@example.com',
}


def _get_or_create_fill_account(role: str) -> Account:
    email = _FILL_ROLE_EMAILS[role]
    try:
        return Account.objects.get(email=email)
    except Account.DoesNotExist:
        with connection.cursor() as cur:
            teacher_id = None
            if role == 'teacher':
                cur.execute("INSERT INTO teachers (name) VALUES ('__fill_test_teacher__') RETURNING id")
                teacher_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, "
                "is_superuser, first_name, last_name, date_joined, token_version) "
                "VALUES (%s, %s, %s, %s, true, false, false, '', '', NOW(), 0) RETURNING id",
                [email, make_password('testpass123'), role, teacher_id],
            )
            acc_id = cur.fetchone()[0]
        return Account.objects.get(pk=acc_id)


def _fill_client(role: str | None) -> APIClient:
    c = APIClient()
    if role is not None:
        account = _get_or_create_fill_account(role)
        refresh = RefreshToken.for_user(account)
        refresh['token_version'] = account.token_version
        c.cookies['access'] = str(refresh.access_token)
    return c


def test_fill_api_requires_auth():
    assert _fill_client(None).get(URL).status_code == 401


@pytest.mark.django_db
def test_fill_api_teacher_forbidden():
    assert _fill_client('teacher').get(URL).status_code == 403


def test_fill_api_manager_envelope_and_content(fill_setup):
    manager = _fill_client('manager')
    resp = manager.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {'rows', 'total', 'page', 'page_size'}
    our = [r for r in body['rows'] if r['group_id'] == fill_setup['group']]
    # строка A (2026-06-02) overdue относительно реального now (текущая дата проекта — 2026-07-20+)
    assert any(r['date'] == '2026-06-02' and r['kind'] == 'planned' for r in our)


def test_fill_api_teacher_id_filter(fill_setup):
    manager = _fill_client('manager')
    resp = manager.get(URL, {'teacher_id': fill_setup['teacher'] + 100000})
    assert resp.status_code == 200
    assert all(r['group_id'] != fill_setup['group'] for r in resp.json()['rows'])


@pytest.mark.django_db
def test_fill_api_invalid_teacher_id_returns_400():
    manager = _fill_client('manager')
    assert manager.get(URL, {'teacher_id': 'abc'}).status_code == 400


def test_fill_api_sort_dir_param(fill_setup):
    manager = _fill_client('manager')
    resp_asc = manager.get(URL, {'sort_dir': 'asc'})
    assert resp_asc.status_code == 200
    resp_bad = manager.get(URL, {'sort_dir': 'sideways'})
    assert resp_bad.status_code == 400
