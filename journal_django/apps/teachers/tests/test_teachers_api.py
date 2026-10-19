"""
E2E тесты для /api/admin/teachers.

Используют DRF APIClient с реальной БД (managed=False, продовая).
Все созданные строки удаляются в teardown.
"""
from __future__ import annotations

import pytest
from django.db import connection

BASE_URL = '/api/admin/teachers'


def _cleanup_teacher(teacher_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


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
# GET /api/admin/teachers — list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_returns_list(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.django_db
def test_list_active_only_by_default(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    for row in resp.json():
        assert row['active'] is True


@pytest.mark.django_db
def test_list_include_inactive(admin_client):
    resp = admin_client.get(BASE_URL + '?include_inactive=1')
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /api/admin/teachers/:id — retrieve
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_retrieve_nonexistent_returns_404(admin_client):
    resp = admin_client.get(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_retrieve_existing_returns_200(admin_client):
    from apps.teachers import repository
    teacher = repository.create_teacher({'name': '__test_api_get_teacher__'})
    try:
        resp = admin_client.get(f"{BASE_URL}/{teacher['id']}")
        assert resp.status_code == 200
        assert resp.json()['id'] == teacher['id']
        assert resp.json()['name'] == '__test_api_get_teacher__'
    finally:
        _cleanup_teacher(teacher['id'])


# ---------------------------------------------------------------------------
# POST /api/admin/teachers — create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_returns_201(superadmin_client):
    resp = superadmin_client.post(BASE_URL, {'name': '__test_post_teacher__'}, format='json')
    if resp.status_code == 201:
        _cleanup_teacher(resp.json()['id'])
    assert resp.status_code == 201


@pytest.mark.django_db
def test_create_missing_name_returns_400(superadmin_client):
    resp = superadmin_client.post(BASE_URL, {}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_create_duplicate_name_returns_409(superadmin_client):
    from apps.teachers import repository
    teacher = repository.create_teacher({'name': '__test_dup_teacher__'})
    try:
        resp = superadmin_client.post(
            BASE_URL, {'name': '__test_dup_teacher__'}, format='json'
        )
        assert resp.status_code == 409
        assert resp.json()['error'] == 'Already exists'
    finally:
        _cleanup_teacher(teacher['id'])


# ---------------------------------------------------------------------------
# PATCH /api/admin/teachers/:id — update
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_patch_returns_200(superadmin_client):
    from apps.teachers import repository
    teacher = repository.create_teacher({'name': '__test_patch_teacher__'})
    try:
        resp = superadmin_client.patch(
            f"{BASE_URL}/{teacher['id']}",
            {'name': '__test_patch_teacher_updated__'},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['name'] == '__test_patch_teacher_updated__'
    finally:
        _cleanup_teacher(teacher['id'])


@pytest.mark.django_db
def test_patch_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.patch(
        f'{BASE_URL}/999999999', {'name': 'ghost'}, format='json'
    )
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_patch_active_false(superadmin_client):
    from apps.teachers import repository
    teacher = repository.create_teacher({'name': '__test_patch_active_teacher__'})
    try:
        resp = superadmin_client.patch(
            f"{BASE_URL}/{teacher['id']}",
            {'active': False},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['active'] is False
    finally:
        _cleanup_teacher(teacher['id'])


# ---------------------------------------------------------------------------
# DELETE /api/admin/teachers/:id — soft delete
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_delete_returns_204(superadmin_client):
    from apps.teachers import repository
    teacher = repository.create_teacher({'name': '__test_del_teacher__'})
    try:
        resp = superadmin_client.delete(f"{BASE_URL}/{teacher['id']}")
        assert resp.status_code == 204
    finally:
        _cleanup_teacher(teacher['id'])


@pytest.mark.django_db
def test_delete_sets_active_false(superadmin_client):
    from apps.teachers import repository
    teacher = repository.create_teacher({'name': '__test_del_active_teacher__'})
    try:
        superadmin_client.delete(f"{BASE_URL}/{teacher['id']}")
        fetched = repository.get_teacher(teacher['id'])
        assert fetched['active'] is False
    finally:
        _cleanup_teacher(teacher['id'])


@pytest.mark.django_db
def test_delete_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.delete(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


# ---------------------------------------------------------------------------
# RBAC: чтение — manager/admin/superadmin; запись — только superadmin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_teachers_read_staff_write_superadmin(manager_client, admin_client, superadmin_client):
    for c in (manager_client, admin_client, superadmin_client):
        assert c.get(BASE_URL).status_code == 200

    payload = {'name': '__test_rbac_teacher__'}
    resp_manager = manager_client.post(BASE_URL, payload, format='json')
    resp_admin = admin_client.post(BASE_URL, payload, format='json')
    assert resp_manager.status_code == 403
    assert resp_admin.status_code == 403

    resp_super = superadmin_client.post(BASE_URL, payload, format='json')
    try:
        assert resp_super.status_code in (200, 201, 409)
    finally:
        if resp_super.status_code == 201:
            _cleanup_teacher(resp_super.json()['id'])


@pytest.mark.django_db
def test_teachers_patch_delete_forbidden_for_manager_and_admin(manager_client, admin_client, superadmin_client):
    from apps.teachers import repository
    teacher = repository.create_teacher({'name': '__test_rbac_teacher_write__'})
    try:
        resp = manager_client.patch(f"{BASE_URL}/{teacher['id']}", {'name': 'x'}, format='json')
        assert resp.status_code == 403
        resp = admin_client.patch(f"{BASE_URL}/{teacher['id']}", {'name': 'x'}, format='json')
        assert resp.status_code == 403
        resp = manager_client.delete(f"{BASE_URL}/{teacher['id']}")
        assert resp.status_code == 403
        resp = admin_client.delete(f"{BASE_URL}/{teacher['id']}")
        assert resp.status_code == 403
    finally:
        _cleanup_teacher(teacher['id'])
