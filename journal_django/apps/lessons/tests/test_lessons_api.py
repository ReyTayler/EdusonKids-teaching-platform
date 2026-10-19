"""
E2E тесты для /api/admin/lessons (DRF APIClient, реальная БД managed=False).

Покрытие:
  - без cookie → 401; role=teacher → 403; manager/admin → 200.
  - GET список: контракт {rows,total,page,page_size}, фильтр group_id.
  - GET /:id → 200 (lesson_date строкой '...'), 404 для отсутствующего.
  - POST → 201 + полный урок; невалидное тело → 400.
  - PATCH → 200 / 404; DELETE → 204 / 404.
  - attendance toggle → 200 {ok:true} / 404.
  - DATE-инвариант: lesson_date в ответе == '2026-03-20' (без сдвига).
  - N+1: список не плодит запросы на group/teacher/direction (assert_num_queries).
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Account
from django.contrib.auth.hashers import make_password

pytestmark = pytest.mark.django_db

BASE_URL = '/api/admin/lessons'

_ROLE_EMAILS = {
    'admin': '__les_admin__@example.com',
    'manager': '__les_manager__@example.com',
    'teacher': '__les_teacher__@example.com',
    'superadmin': '__les_super__@example.com',
}
_CREATED_IDS: list[int] = []


def _get_or_create_account(role: str) -> 'Account':
    """Возвращает аккаунт для роли, создаёт при необходимости."""
    email = _ROLE_EMAILS[role]
    try:
        return Account.objects.get(email=email)
    except Account.DoesNotExist:
        from django.db import connection as _conn
        with _conn.cursor() as cur:
            # role='teacher' требует teacher_id (CHECK accounts_teacher_role_check).
            teacher_id = None
            if role == 'teacher':
                cur.execute("INSERT INTO teachers (name) VALUES ('__lessons_teacher__') RETURNING id")
                teacher_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, is_superuser, first_name, last_name, date_joined, token_version) "
                "VALUES (%s, %s, %s, %s, true, false, false, '', '', NOW(), 0) RETURNING id",
                [email, make_password('testpass123'), role, teacher_id],
            )
            acc_id = cur.fetchone()[0]
            _CREATED_IDS.append(acc_id)
        return Account.objects.get(pk=acc_id)


def _client(role: str | None) -> APIClient:
    c = APIClient()
    if role is not None:
        account = _get_or_create_account(role)
        refresh = RefreshToken.for_user(account)
        refresh['token_version'] = account.token_version
        c.cookies['access'] = str(refresh.access_token)
    return c


def _delete_lesson(lesson_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


def _create_lesson(group_id: int, teacher_id: int, date: str = '2026-03-20') -> int:
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number,
                                 lesson_duration_minutes, lesson_type, submitted_by_token)
            VALUES (%s, %s, %s, 1, 60, 'regular', 'test')
            RETURNING id
            """,
            [group_id, teacher_id, date],
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_list_requires_auth():
    resp = _client(None).get(BASE_URL)
    assert resp.status_code == 401


def test_list_teacher_forbidden():
    resp = _client('teacher').get(BASE_URL)
    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['manager', 'admin'])
def test_list_allowed_roles(role):
    resp = _client(role).get(BASE_URL)
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {'rows', 'total', 'page', 'page_size'}


# ---------------------------------------------------------------------------
# GET список / detail
# ---------------------------------------------------------------------------

def test_list_filter_group_id(group_fixture, teacher_id_fixture):
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    try:
        resp = _client('manager').get(BASE_URL, {'group_id': group_fixture})
        assert resp.status_code == 200
        body = resp.json()
        assert body['total'] == 1
        assert body['rows'][0]['id'] == lesson_id
    finally:
        _delete_lesson(lesson_id)


def test_get_detail_date_invariant(group_fixture, teacher_id_fixture):
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture, '2026-03-20')
    try:
        resp = _client('manager').get(f'{BASE_URL}/{lesson_id}')
        assert resp.status_code == 200
        body = resp.json()
        # DATE-инвариант: строка без сдвига на день
        assert body['lesson_date'] == '2026-03-20'
        assert 'attendance' in body
        assert 'payroll' in body
    finally:
        _delete_lesson(lesson_id)


def test_get_detail_404():
    resp = _client('manager').get(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


def test_page_size_zero_falls_back_to_default():
    # Express: Number("0") || 50 → 50. Django должен совпасть (а не дать 1).
    resp = _client('manager').get(BASE_URL, {'page_size': 0})
    assert resp.status_code == 200
    assert resp.json()['page_size'] == 50


# ---------------------------------------------------------------------------
# POST / PATCH / DELETE
# ---------------------------------------------------------------------------

def test_post_creates_lesson(group_fixture, teacher_id_fixture, student_fixture, membership_fixture):
    payload = {
        'lesson_date': '2026-03-21',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    }
    resp = _client('admin').post(BASE_URL, payload, format='json')
    assert resp.status_code == 201
    body = resp.json()
    lesson_id = body['id']
    try:
        assert body['lesson_date'] == '2026-03-21'
        assert len(body['attendance']) == 1
    finally:
        _delete_lesson(lesson_id)


def test_post_free_outcome_pays_teacher_not_student(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """POST с is_free=true (бесплатное занятие, один ученик) → урок создаётся,
    преподавателю платят как обычно (total=1, present=1 → 500). Строка
    lesson_attendance.is_free=true (баланс УЧЕНИКА не спишется — это проверяет
    finances). Сквозная проверка сериализатор→сервис→БД."""
    payload = {
        'lesson_date': '2026-03-22',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True, 'is_free': True}],
    }
    resp = _client('admin').post(BASE_URL, payload, format='json')
    assert resp.status_code == 201
    lesson_id = resp.json()['id']
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT total_students, present_count, payment FROM payroll '
                        'WHERE lesson_id = %s', [lesson_id])
            total, present, payment = cur.fetchone()
            assert total == 1 and present == 1 and float(payment) == 500.0
            cur.execute('SELECT present, is_free FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lesson_id, student_fixture])
            assert cur.fetchone() == (True, True)
    finally:
        _delete_lesson(lesson_id)


def test_post_invalid_body_400(group_fixture, teacher_id_fixture):
    payload = {
        'lesson_date': 'not-a-date',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
    }
    resp = _client('admin').post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


def test_post_blocked_when_student_has_no_paid_balance(
    group_fixture, teacher_id_fixture, student_fixture,
):
    """Ученик без оплаченных уроков (membership без payments) + present:true →
    400 {'error': ...}, урок не создаётся (UnpaidAttendanceBlocked → view)."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, 0, true) RETURNING id',
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
    payload = {
        'lesson_date': '2026-03-28',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    }
    try:
        resp = _client('admin').post(BASE_URL, payload, format='json')
        assert resp.status_code == 400
        assert 'error' in resp.json()
        with connection.cursor() as cur:
            cur.execute(
                'SELECT COUNT(*) FROM lessons WHERE group_id = %s AND lesson_date = %s',
                [group_fixture, '2026-03-28'],
            )
            assert cur.fetchone()[0] == 0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def test_patch_lesson(group_fixture, teacher_id_fixture):
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    try:
        resp = _client('admin').patch(
            f'{BASE_URL}/{lesson_id}', {'lesson_type': 'reschedule'}, format='json'
        )
        assert resp.status_code == 200
        assert resp.json()['lesson_type'] == 'reschedule'
    finally:
        _delete_lesson(lesson_id)


def test_patch_404():
    resp = _client('admin').patch(
        f'{BASE_URL}/999999999', {'lesson_type': 'regular'}, format='json'
    )
    assert resp.status_code == 404


def test_delete_lesson(group_fixture, teacher_id_fixture):
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    resp = _client('admin').delete(f'{BASE_URL}/{lesson_id}')
    assert resp.status_code == 204
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM lessons WHERE id = %s', [lesson_id])
        assert cur.fetchone()[0] == 0


def test_delete_404():
    resp = _client('admin').delete(f'{BASE_URL}/999999999')
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# attendance toggle
# ---------------------------------------------------------------------------

def test_attendance_toggle(group_fixture, teacher_id_fixture, student_fixture, membership_fixture):
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    try:
        resp = _client('admin').patch(
            f'{BASE_URL}/{lesson_id}/attendance/{student_fixture}',
            {'present': True},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json() == {'ok': True}
    finally:
        _delete_lesson(lesson_id)


def test_attendance_toggle_404_missing_lesson(student_fixture):
    resp = _client('admin').patch(
        f'{BASE_URL}/999999999/attendance/{student_fixture}',
        {'present': True},
        format='json',
    )
    assert resp.status_code == 404


def test_attendance_toggle_blocked_when_no_paid_balance(
    group_fixture, teacher_id_fixture, student_fixture,
):
    """Ученик без оплаченных уроков (membership без payments) + present:true →
    400 {'error': ...}, посещаемость не меняется (UnpaidAttendanceBlocked → view)."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, 0, true) RETURNING id',
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    try:
        resp = _client('admin').patch(
            f'{BASE_URL}/{lesson_id}/attendance/{student_fixture}',
            {'present': True},
            format='json',
        )
        assert resp.status_code == 400
        assert 'error' in resp.json()
        with connection.cursor() as cur:
            cur.execute(
                'SELECT COUNT(*) FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                [lesson_id, student_fixture],
            )
            assert cur.fetchone()[0] == 0
    finally:
        _delete_lesson(lesson_id)
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def test_attendance_cell_locked_by_transfer_409(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) VALUES ('__les_api_locked_src__', %s, %s, false, 60, true, 0) "
            "RETURNING id",
            [direction_fixture, teacher_id_fixture],
        )
        src_group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 5, false) RETURNING id",
            [src_group_id, student_fixture],
        )
        src_membership_id = cur.fetchone()[0]
        cur.execute(
            'UPDATE group_memberships SET transferred_from_id = %s '
            'WHERE group_id = %s AND student_id = %s',
            [src_membership_id, group_fixture, student_fixture],
        )
    lesson_id = _create_lesson(group_fixture, teacher_id_fixture)
    try:
        resp = _client('admin').patch(
            f'{BASE_URL}/{lesson_id}/attendance/{student_fixture}', {'present': True}, format='json',
        )
        assert resp.status_code == 409
    finally:
        _delete_lesson(lesson_id)
        with connection.cursor() as cur:
            cur.execute('UPDATE group_memberships SET transferred_from_id = NULL '
                        'WHERE group_id = %s AND student_id = %s', [group_fixture, student_fixture])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [src_membership_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [src_group_id])


# ---------------------------------------------------------------------------
# N+1
# ---------------------------------------------------------------------------

def test_list_no_n_plus_1(group_fixture, teacher_id_fixture):
    # Создаём 3 урока — список должен выполнять фиксированное число запросов
    # (COUNT + rows), а не по запросу на каждый JOIN group/teacher/direction.
    ids = [_create_lesson(group_fixture, teacher_id_fixture, f'2026-03-2{i}') for i in range(3)]
    try:
        client = _client('manager')
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(BASE_URL, {'group_id': group_fixture})
            assert resp.status_code == 200
            assert resp.json()['total'] == 3
        # COUNT + rows = 2 запроса данных (плюс возможные no-op). Жёстко ≤ 4.
        assert len(ctx.captured_queries) <= 4
    finally:
        for lid in ids:
            _delete_lesson(lid)


# ---------------------------------------------------------------------------
# RBAC: manager — только просмотр; запись/посещаемость — admin/superadmin;
# зарплата за урок (payroll) видна только superadmin.
# ---------------------------------------------------------------------------

def test_lessons_get_allowed_for_staff():
    for role in ('manager', 'admin', 'superadmin'):
        assert _client(role).get(BASE_URL).status_code == 200


def test_lessons_write_forbidden_for_manager(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture
):
    payload = {
        'lesson_date': '2026-03-25',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    }
    resp_manager = _client('manager').post(BASE_URL, payload, format='json')
    assert resp_manager.status_code == 403

    resp_admin = _client('admin').post(BASE_URL, payload, format='json')
    assert resp_admin.status_code in (200, 201, 400, 409)
    if resp_admin.status_code == 201:
        _delete_lesson(resp_admin.json()['id'])


def test_attendance_toggle_forbidden_for_manager(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture
):
    payload = {
        'lesson_date': '2026-03-26',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    }
    created = _client('superadmin').post(BASE_URL, payload, format='json')
    assert created.status_code == 201
    lesson_id = created.json()['id']
    try:
        detail = _client('superadmin').get(f'{BASE_URL}/{lesson_id}').json()
        sid = detail['attendance'][0]['student_id']
        url = f'{BASE_URL}/{lesson_id}/attendance/{sid}'
        assert _client('manager').patch(url, {'present': True}, format='json').status_code == 403
        assert _client('admin').patch(url, {'present': True}, format='json').status_code in (200, 404)
    finally:
        _delete_lesson(lesson_id)


def test_payroll_visible_only_to_superadmin(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture
):
    payload = {
        'lesson_date': '2026-03-27',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    }
    created = _client('superadmin').post(BASE_URL, payload, format='json')
    assert created.status_code == 201
    lesson_id = created.json()['id']
    try:
        body_admin = _client('admin').get(f'{BASE_URL}/{lesson_id}').json()
        body_super = _client('superadmin').get(f'{BASE_URL}/{lesson_id}').json()
        assert body_admin.get('payroll') is None
        assert body_super.get('payroll') is not None

        rows_admin = _client('admin').get(BASE_URL, {'group_id': group_fixture}).json()['rows']
        assert all('payment' not in r and 'penalty' not in r for r in rows_admin)
    finally:
        _delete_lesson(lesson_id)


def _make_extra_lesson() -> tuple[int, int, int, int]:
    """teacher+direction+group + Lesson(lesson_type='extra'). → (lesson_id, group_id, teacher_id, direction_id)."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__les_extra_t__') RETURNING id")
        tid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, active) "
            "VALUES ('__les_extra_d__', true) RETURNING id")
        did = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__les_extra_g__', %s, %s, false, 60, true, 0) RETURNING id", [did, tid])
        gid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,'2026-04-01',1,60,'extra','__les_extra__') RETURNING id", [gid, tid])
        lid = cur.fetchone()[0]
    return lid, gid, tid, did


def _cleanup_extra(lid: int, gid: int, tid: int, did: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        cur.execute('DELETE FROM groups WHERE id = %s', [gid])
        cur.execute('DELETE FROM directions WHERE id = %s', [did])
        cur.execute('DELETE FROM teachers WHERE id = %s', [tid])


def test_delete_extra_lesson_blocked_409():
    """Факт доп.урока (lesson_type='extra') нельзя удалить через общий CRUD → 409, урок цел."""
    lid, gid, tid, did = _make_extra_lesson()
    try:
        resp = _client('admin').delete(f'{BASE_URL}/{lid}')
        assert resp.status_code == 409
        assert 'error' in resp.json()
        with connection.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM lessons WHERE id = %s', [lid])
            assert cur.fetchone()[0] == 1
    finally:
        _cleanup_extra(lid, gid, tid, did)


def test_patch_extra_lesson_blocked_409():
    """PATCH мета факта доп.урока через общий CRUD → 409."""
    lid, gid, tid, did = _make_extra_lesson()
    try:
        resp = _client('admin').patch(f'{BASE_URL}/{lid}', {'record_url': 'https://x.test'}, format='json')
        assert resp.status_code == 409
        assert 'error' in resp.json()
    finally:
        _cleanup_extra(lid, gid, tid, did)


def test_patch_cannot_set_lesson_type_to_extra_400():
    """Обычный урок нельзя «превратить» в системный (extra) через общий PATCH:
    lesson_type='extra' не входит в choices LessonUpdateSerializer → 400 (вектор
    закрыт на слое сериализатора; сервис-гард на существующий системный урок —
    defense-in-depth, покрыт test_patch_extra_lesson_blocked_409)."""
    lid, gid, tid, did = _make_extra_lesson()
    with connection.cursor() as cur:
        cur.execute("UPDATE lessons SET lesson_type = 'regular' WHERE id = %s", [lid])
    try:
        resp = _client('admin').patch(f'{BASE_URL}/{lid}', {'lesson_type': 'extra'}, format='json')
        assert resp.status_code == 400
    finally:
        _cleanup_extra(lid, gid, tid, did)


def test_attendance_toggle_on_extra_lesson_blocked_409():
    """Тоггл ячейки посещаемости на факте доп.урока через общий CRUD → 409."""
    lid, gid, tid, did = _make_extra_lesson()
    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_extra_s__') RETURNING id")
        sid = cur.fetchone()[0]
    try:
        resp = _client('admin').patch(f'{BASE_URL}/{lid}/attendance/{sid}', {'present': True}, format='json')
        assert resp.status_code == 409
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE student_id = %s', [sid])
            cur.execute('DELETE FROM students WHERE id = %s', [sid])
        _cleanup_extra(lid, gid, tid, did)


# ---------------------------------------------------------------------------
# escape hatch: admin-путь НЕ блокируется незакрытыми занятиями группы
# ---------------------------------------------------------------------------

def test_post_not_blocked_by_unfilled_plan(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Admin SPA — escape hatch: ручное создание урока НЕ блокируется незакрытыми
    занятиями группы, иначе разблокировать группу будет некому. Гард живёт в
    teacher_spa.submit_lesson, а не в ядре record_lesson — этот тест страхует от
    переноса гарда в ядро."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, status, created_at, updated_at) '
            "VALUES (%s, 1, 1, '2026-03-14', '10:00', %s, 'pending', NOW(), NOW())",
            [group_fixture, teacher_id_fixture],
        )
    payload = {
        'lesson_date': '2026-03-21',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    }
    try:
        resp = _client('admin').post(BASE_URL, payload, format='json')
        assert resp.status_code == 201
        _delete_lesson(resp.json()['id'])
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])
