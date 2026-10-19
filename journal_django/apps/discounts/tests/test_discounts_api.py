"""
E2E тесты для /api/admin/discounts.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

BASE_URL = '/api/admin/discounts'


def _cleanup_discount(discount_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM discounts WHERE id = %s', [discount_id])


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
def test_list_active_only_by_default(admin_client):
    resp = admin_client.get(BASE_URL)
    for row in resp.json():
        assert row['active'] is True


# ---------------------------------------------------------------------------
# GET /:id
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_retrieve_nonexistent_returns_404(admin_client):
    resp = admin_client.get(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_retrieve_existing(admin_client):
    from apps.discounts import repository
    d = repository.create_discount({'name': '__test_api_get_disc__', 'amount': Decimal('0.1')})
    try:
        resp = admin_client.get(f"{BASE_URL}/{d['id']}")
        assert resp.status_code == 200
        assert resp.json()['id'] == d['id']
    finally:
        _cleanup_discount(d['id'])


# ---------------------------------------------------------------------------
# POST — create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_returns_201(superadmin_client):
    resp = superadmin_client.post(
        BASE_URL, {'name': '__test_post_disc__', 'amount': 0.1}, format='json'
    )
    if resp.status_code == 201:
        _cleanup_discount(resp.json()['id'])
    assert resp.status_code == 201


@pytest.mark.django_db
def test_create_missing_name_returns_400(superadmin_client):
    resp = superadmin_client.post(BASE_URL, {'amount': 0.1}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_amount_out_of_range_returns_400(superadmin_client):
    resp = superadmin_client.post(
        BASE_URL, {'name': '__bad_amount__', 'amount': 1.5}, format='json'
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH — update
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_patch_returns_200(superadmin_client):
    from apps.discounts import repository
    d = repository.create_discount({'name': '__test_patch_disc__', 'amount': Decimal('0.1')})
    try:
        resp = superadmin_client.patch(
            f"{BASE_URL}/{d['id']}",
            {'name': '__test_patch_disc_upd__'},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['name'] == '__test_patch_disc_upd__'
    finally:
        _cleanup_discount(d['id'])


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
def test_delete_returns_204(superadmin_client):
    from apps.discounts import repository
    d = repository.create_discount({'name': '__test_del_disc__', 'amount': Decimal('0.1')})
    try:
        resp = superadmin_client.delete(f"{BASE_URL}/{d['id']}")
        assert resp.status_code == 204
    finally:
        _cleanup_discount(d['id'])


@pytest.mark.django_db
def test_delete_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.delete(f'{BASE_URL}/999999999')
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RBAC: чтение — manager/admin/superadmin; запись — только superadmin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_discounts_read_staff_write_superadmin(manager_client, admin_client, superadmin_client):
    for c in (manager_client, admin_client, superadmin_client):
        assert c.get(BASE_URL).status_code == 200

    payload = {'name': '__test_rbac_discount__', 'amount': 0.1}
    resp_manager = manager_client.post(BASE_URL, payload, format='json')
    resp_admin = admin_client.post(BASE_URL, payload, format='json')
    assert resp_manager.status_code == 403
    assert resp_admin.status_code == 403

    resp_super = superadmin_client.post(BASE_URL, payload, format='json')
    try:
        assert resp_super.status_code in (200, 201, 409)
    finally:
        if resp_super.status_code == 201:
            _cleanup_discount(resp_super.json()['id'])


@pytest.mark.django_db
def test_discounts_patch_delete_forbidden_for_manager_and_admin(manager_client, admin_client, superadmin_client):
    from apps.discounts import repository
    d = repository.create_discount({'name': '__test_rbac_discount_write__', 'amount': Decimal('0.1')})
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
        _cleanup_discount(d['id'])
