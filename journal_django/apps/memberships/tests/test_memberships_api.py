"""
E2E тесты для /api/admin/memberships.

Используют DRF APIClient с реальной БД (managed=False, продовая).
Все созданные строки удаляются в teardown (прямой DELETE через connection).

Cookie:
  Генерируются через _make_cookie() из conftest.py — идентично Node.js sign().
  ADMIN_COOKIE_SECRET переопределяется через pytest-django фикстуру settings.

Тестируемые кейсы:
  - без cookie → 401
  - cookie role=teacher → 403
  - cookie role=admin → 200 (список)
  - cookie role=manager → 200 (список)
  - GET список — массив (не dict с rows/total/page/page_size)
  - GET фильтры: group_id, student_id, include_inactive=1
  - POST → 201, UPSERT (повторный POST → 201 с реактивацией)
  - PATCH → 200/404
  - DELETE → 204/404

RBAC на запись — в test_memberships_rbac.py (самодостаточный seed).
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.core.tests.minimal_school import ensure_minimal_school


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = '/api/admin/memberships'


def _get_valid_group_id() -> int:
    """Активная группа минимальной школы (в ней уже есть одно активное членство)."""
    return ensure_minimal_school()['group_id']


def _get_valid_student_id() -> int:
    """Ученик без членства: тесты записи сами создают пару «группа + ученик» (ждут 201)."""
    return ensure_minimal_school()['free_student_id']


def _cleanup_pair(group_id: int, student_id: int) -> None:
    """Удалить все строки для пары (group_id, student_id)."""
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM group_memberships WHERE group_id = %s AND student_id = %s',
            [group_id, student_id],
        )


def _cleanup_by_id(membership_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_cookie_returns_401(anon_client):
    resp = anon_client.get(BASE_URL)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_teacher_cookie_returns_403(teacher_client):
    resp = teacher_client.get(BASE_URL)
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_cookie_returns_200(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_manager_cookie_returns_200(manager_client):
    resp = manager_client.get(BASE_URL)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/admin/memberships — list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_returns_array(admin_client):
    """Ответ — массив, не dict {rows, total, page, page_size}."""
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.django_db
def test_list_not_paginated(admin_client):
    """Ответ не содержит ключей пагинатора."""
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    data = resp.json()
    assert not isinstance(data, dict)


@pytest.mark.django_db
def test_list_default_only_active(admin_client):
    """По умолчанию include_inactive не передан — только active=true."""
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    for row in resp.json():
        assert row['active'] is True


@pytest.mark.django_db
def test_list_include_inactive(admin_client):
    """include_inactive=1 возвращает все записи."""
    resp_active = admin_client.get(BASE_URL)
    resp_all = admin_client.get(BASE_URL + '?include_inactive=1')
    assert resp_all.status_code == 200
    assert len(resp_all.json()) >= len(resp_active.json())


@pytest.mark.django_db
def test_list_filter_by_group_id(admin_client):
    group_id = _get_valid_group_id()
    resp = admin_client.get(BASE_URL + f'?group_id={group_id}&include_inactive=1')
    assert resp.status_code == 200
    assert resp.json()
    for row in resp.json():
        assert row['group_id'] == group_id


@pytest.mark.django_db
def test_list_filter_by_student_id(admin_client):
    student_id = ensure_minimal_school()['student_id']  # ученик с членством
    resp = admin_client.get(BASE_URL + f'?student_id={student_id}&include_inactive=1')
    assert resp.status_code == 200
    assert resp.json()
    for row in resp.json():
        assert row['student_id'] == student_id


@pytest.mark.django_db
def test_list_rows_have_group_name(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    rows = resp.json()
    if rows:
        assert 'group_name' in rows[0]


@pytest.mark.django_db
def test_list_rows_have_student_name(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    rows = resp.json()
    if rows:
        assert 'student_name' in rows[0]


@pytest.mark.django_db
def test_list_filter_nonexistent_group_empty(admin_client):
    resp = admin_client.get(BASE_URL + '?group_id=999999999')
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/admin/memberships — create / upsert
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_post_returns_201(superadmin_client):
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    try:
        resp = superadmin_client.post(
            BASE_URL,
            {'group_id': group_id, 'student_id': student_id},
            format='json',
        )
        assert resp.status_code == 201
    finally:
        _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_post_creates_membership(superadmin_client):
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    try:
        resp = superadmin_client.post(
            BASE_URL,
            {'group_id': group_id, 'student_id': student_id},
            format='json',
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data['group_id'] == group_id
        assert data['student_id'] == student_id
        assert data['active'] is True
    finally:
        _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_post_with_start_date(superadmin_client):
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    try:
        resp = superadmin_client.post(
            BASE_URL,
            {'group_id': group_id, 'student_id': student_id, 'start_date': '2025-09-01'},
            format='json',
        )
        assert resp.status_code == 201
        assert resp.json()['start_date'] == '2025-09-01'
    finally:
        _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_post_upsert_reactivation(superadmin_client):
    """
    UPSERT: повторный POST той же пары после удаления → 201 с active=true.

    Не должен возвращать 409 и не должен создавать дубль.
    """
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    try:
        # Создаём
        resp1 = superadmin_client.post(
            BASE_URL,
            {'group_id': group_id, 'student_id': student_id},
            format='json',
        )
        assert resp1.status_code == 201
        membership_id = resp1.json()['id']

        # Деактивируем напрямую
        with connection.cursor() as cur:
            cur.execute(
                'UPDATE group_memberships SET active = false WHERE id = %s',
                [membership_id],
            )

        # Повторный POST → реактивация
        resp2 = superadmin_client.post(
            BASE_URL,
            {'group_id': group_id, 'student_id': student_id},
            format='json',
        )
        assert resp2.status_code == 201
        data2 = resp2.json()
        assert data2['id'] == membership_id  # тот же id
        assert data2['active'] is True
    finally:
        _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_post_remaining_is_ignored(superadmin_client):
    """
    remaining больше нельзя выставить руками — сериализатор его не принимает,
    POST с этим полем в теле не должен ничего сломать и не должен повлиять
    на вычисляемое значение в ответе (симметрично test_patch_remaining_is_ignored).
    """
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    try:
        resp = superadmin_client.post(
            BASE_URL,
            {'group_id': group_id, 'student_id': student_id, 'remaining': 999},
            format='json',
        )
        assert resp.status_code == 201
        assert resp.json()['remaining'] != 999
    finally:
        _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_post_missing_group_id_returns_400(superadmin_client):
    student_id = _get_valid_student_id()
    resp = superadmin_client.post(BASE_URL, {'student_id': student_id}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_post_missing_student_id_returns_400(superadmin_client):
    group_id = _get_valid_group_id()
    resp = superadmin_client.post(BASE_URL, {'group_id': group_id}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_post_invalid_group_id_zero_returns_400(superadmin_client):
    student_id = _get_valid_student_id()
    resp = superadmin_client.post(
        BASE_URL,
        {'group_id': 0, 'student_id': student_id},
        format='json',
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/admin/memberships/:id — update
# ---------------------------------------------------------------------------

@pytest.fixture
def existing_membership():
    """Создаёт membership и удаляет после теста."""
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true)
            ON CONFLICT (group_id, student_id) DO UPDATE SET active = true
            RETURNING *
            """,
            [group_id, student_id],
        )
        columns = [col[0] for col in cur.description]
        row = dict(zip(columns, cur.fetchone()))
    yield row
    _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_patch_returns_200(superadmin_client, existing_membership):
    resp = superadmin_client.patch(
        f"{BASE_URL}/{existing_membership['id']}",
        {'active': False},
        format='json',
    )
    assert resp.status_code == 200


@pytest.mark.django_db
def test_patch_updates_active(superadmin_client, existing_membership):
    resp = superadmin_client.patch(
        f"{BASE_URL}/{existing_membership['id']}",
        {'active': False},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['active'] is False


@pytest.mark.django_db
def test_patch_updates_start_date(superadmin_client, existing_membership):
    resp = superadmin_client.patch(
        f"{BASE_URL}/{existing_membership['id']}",
        {'start_date': '2025-06-01'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['start_date'] == '2025-06-01'


@pytest.mark.django_db
def test_patch_remaining_is_ignored(superadmin_client, existing_membership):
    """
    remaining больше нельзя выставить руками — сериализатор его не принимает,
    PATCH с этим полем в теле не должен ничего сломать и не должен повлиять
    на вычисляемое значение в ответе.
    """
    resp = superadmin_client.patch(
        f"{BASE_URL}/{existing_membership['id']}",
        {'remaining': 999},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['remaining'] != 999


@pytest.mark.django_db
def test_patch_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.patch(
        f'{BASE_URL}/999999999',
        {'active': False},
        format='json',
    )
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


# ---------------------------------------------------------------------------
# DELETE /api/admin/memberships/:id — soft delete
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_delete_returns_204(superadmin_client):
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true)
            ON CONFLICT (group_id, student_id) DO UPDATE SET active = true
            RETURNING id
            """,
            [group_id, student_id],
        )
        membership_id = cur.fetchone()[0]
    try:
        resp = superadmin_client.delete(f'{BASE_URL}/{membership_id}')
        assert resp.status_code == 204
    finally:
        _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_delete_sets_active_false(superadmin_client):
    group_id = _get_valid_group_id()
    student_id = _get_valid_student_id()
    _cleanup_pair(group_id, student_id)
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true)
            ON CONFLICT (group_id, student_id) DO UPDATE SET active = true
            RETURNING id
            """,
            [group_id, student_id],
        )
        membership_id = cur.fetchone()[0]
    try:
        superadmin_client.delete(f'{BASE_URL}/{membership_id}')
        # Проверяем через БД
        with connection.cursor() as cur:
            cur.execute(
                'SELECT active FROM group_memberships WHERE id = %s',
                [membership_id],
            )
            active = cur.fetchone()[0]
        assert active is False
    finally:
        _cleanup_pair(group_id, student_id)


@pytest.mark.django_db
def test_delete_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.delete(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


# ---------------------------------------------------------------------------
# DELETE — гейт доп.уроков при снятии членства (см. apps.extra_lessons):
# назначенный доп.урок блокирует (409 + code), pending удаляется автоматически.
# ---------------------------------------------------------------------------

@pytest.fixture
def student_in_group():
    """Самодостаточный сетап (свои direction/teacher/group/student + активное
    членство) — не зависит от seed-данных БД. Отдаёт
    (membership_id, student_id, group_id, teacher_id)."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__mem_guard_dir__', 8, true) RETURNING id")
        direction_id = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__mem_guard_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__mem_guard_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [direction_id, teacher_id])
        group_id = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__mem_guard_student__') RETURNING id")
        student_id = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true) RETURNING id", [group_id, student_id])
        membership_id = cur.fetchone()[0]
    yield membership_id, student_id, group_id, teacher_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


def _insert_missed_lesson(group_id, teacher_id, token):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) "
            "VALUES (%s, %s, '2026-05-01', 1, 90, 'regular', now(), %s) RETURNING id",
            [group_id, teacher_id, token])
        return cur.fetchone()[0]


def _insert_resolution(missed_lesson_id, student_id, status):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO absence_resolutions (missed_lesson_id, student_id, status, created_at) "
            "VALUES (%s, %s, %s, now()) RETURNING id",
            [missed_lesson_id, student_id, status])
        return cur.fetchone()[0]


@pytest.mark.django_db
def test_delete_blocked_by_scheduled_makeup(superadmin_client, student_in_group):
    """Назначенный доп.урок (makeup_scheduled) по пропуску в группе блокирует
    снятие членства: 409 + code, членство остаётся активным."""
    membership_id, student_id, group_id, teacher_id = student_in_group
    lesson_id = _insert_missed_lesson(group_id, teacher_id, '__mem_guard_sched__')
    try:
        _insert_resolution(lesson_id, student_id, 'makeup_scheduled')

        resp = superadmin_client.delete(f'{BASE_URL}/{membership_id}')
        assert resp.status_code == 409
        assert resp.json()['code'] == 'membership_has_scheduled_makeups'

        with connection.cursor() as cur:
            cur.execute('SELECT active FROM group_memberships WHERE id = %s', [membership_id])
            assert cur.fetchone()[0] is True  # членство не снято
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])  # CASCADE резолюции


@pytest.mark.django_db
def test_delete_auto_deletes_pending(superadmin_client, student_in_group):
    """pending («Ждёт решения») по пропуску в группе удаляется автоматически при
    снятии членства: 204, членство снято, резолюция исчезла."""
    membership_id, student_id, group_id, teacher_id = student_in_group
    lesson_id = _insert_missed_lesson(group_id, teacher_id, '__mem_guard_pending__')
    try:
        _insert_resolution(lesson_id, student_id, 'pending')

        resp = superadmin_client.delete(f'{BASE_URL}/{membership_id}')
        assert resp.status_code == 204

        with connection.cursor() as cur:
            cur.execute('SELECT active FROM group_memberships WHERE id = %s', [membership_id])
            assert cur.fetchone()[0] is False  # членство снято
            cur.execute('SELECT COUNT(*) FROM absence_resolutions '
                        'WHERE missed_lesson_id = %s AND student_id = %s', [lesson_id, student_id])
            assert cur.fetchone()[0] == 0  # pending удалён
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


# RBAC записи вынесен в test_memberships_rbac.py: тамошние фикстуры сеют свои
# группы/учеников, а здешние хелперы берут их из БД и скипаются на пустой базе.
