"""
E2E тесты для /api/admin/directions.
"""
from __future__ import annotations

import pytest
from django.db import connection

BASE_URL = '/api/admin/directions'


def _cleanup_direction(direction_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


def _direction_payload(**overrides) -> dict:
    return {
        'name': '__test_api_direction__',
        **overrides,
    }


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
def test_admin_returns_200(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_manager_returns_200(manager_client):
    resp = manager_client.get(BASE_URL)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_returns_list(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.django_db
def test_list_include_inactive(admin_client):
    resp = admin_client.get(BASE_URL + '?include_inactive=1')
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /:id
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_retrieve_nonexistent_returns_404(admin_client):
    resp = admin_client.get(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_retrieve_existing_returns_200(admin_client):
    from apps.directions import repository
    d = repository.create_direction({
        'name': '__test_api_get_dir__',
    })
    try:
        resp = admin_client.get(f"{BASE_URL}/{d['id']}")
        assert resp.status_code == 200
        assert resp.json()['id'] == d['id']
    finally:
        _cleanup_direction(d['id'])


# ---------------------------------------------------------------------------
# POST — create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_returns_201(superadmin_client):
    payload = _direction_payload(name='__test_post_dir_201__')
    resp = superadmin_client.post(BASE_URL, payload, format='json')
    if resp.status_code == 201:
        _cleanup_direction(resp.json()['id'])
    assert resp.status_code == 201


@pytest.mark.django_db
def test_create_missing_required_returns_400(superadmin_client):
    # name — единственное обязательное поле; без него → 400.
    resp = superadmin_client.post(BASE_URL, {'total_lessons': 8}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_invalid_color_returns_400(superadmin_client):
    payload = _direction_payload(name='__bad_color__', color='red')
    resp = superadmin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_create_duplicate_name_returns_409(superadmin_client):
    from apps.directions import repository
    d = repository.create_direction({
        'name': '__test_dup_dir__',
    })
    try:
        resp = superadmin_client.post(
            BASE_URL,
            {'name': '__test_dup_dir__'},
            format='json',
        )
        assert resp.status_code == 409
    finally:
        _cleanup_direction(d['id'])


# ---------------------------------------------------------------------------
# PATCH — update
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_patch_returns_200(superadmin_client):
    from apps.directions import repository
    d = repository.create_direction({
        'name': '__test_patch_dir__',
    })
    try:
        resp = superadmin_client.patch(
            f"{BASE_URL}/{d['id']}",
            {'name': '__test_patch_dir_upd__'},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['name'] == '__test_patch_dir_upd__'
    finally:
        _cleanup_direction(d['id'])


@pytest.mark.django_db
def test_patch_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.patch(
        f'{BASE_URL}/999999999', {'name': 'X'}, format='json'
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE — soft delete
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_delete_no_payments_returns_204(superadmin_client):
    from apps.directions import repository
    d = repository.create_direction({
        'name': '__test_del_dir_204__',
    })
    try:
        resp = superadmin_client.delete(f"{BASE_URL}/{d['id']}")
        assert resp.status_code == 204
    finally:
        _cleanup_direction(d['id'])


@pytest.mark.django_db
def test_delete_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.delete(f'{BASE_URL}/999999999')
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RBAC: чтение — manager/admin/superadmin; запись — только superadmin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_directions_read_staff_write_superadmin(manager_client, admin_client, superadmin_client):
    for c in (manager_client, admin_client, superadmin_client):
        assert c.get(BASE_URL).status_code == 200

    payload = _direction_payload(name='__test_rbac_direction__')
    resp_manager = manager_client.post(BASE_URL, payload, format='json')
    resp_admin = admin_client.post(BASE_URL, payload, format='json')
    assert resp_manager.status_code == 403
    assert resp_admin.status_code == 403

    resp_super = superadmin_client.post(BASE_URL, payload, format='json')
    try:
        assert resp_super.status_code in (200, 201, 409)
    finally:
        if resp_super.status_code == 201:
            _cleanup_direction(resp_super.json()['id'])


@pytest.mark.django_db
def test_directions_patch_delete_forbidden_for_manager_and_admin(manager_client, admin_client, superadmin_client):
    from apps.directions import repository
    d = repository.create_direction({
        'name': '__test_rbac_direction_write__',
    })
    try:
        resp = manager_client.patch(f"{BASE_URL}/{d['id']}", {'name': 'x'}, format='json')
        assert resp.status_code == 403
        resp = admin_client.patch(f"{BASE_URL}/{d['id']}", {'name': 'x'}, format='json')
        assert resp.status_code == 403
        resp = manager_client.delete(f"{BASE_URL}/{d['id']}")
        assert resp.status_code == 403
        resp = admin_client.delete(f"{BASE_URL}/{d['id']}")
        assert resp.status_code == 403
    finally:
        _cleanup_direction(d['id'])
