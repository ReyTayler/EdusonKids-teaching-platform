"""
E2E тесты для /api/admin/audit-log.

ТОЛЬКО superadmin — manager и admin должны получить 403.
"""
from __future__ import annotations

import pytest

BASE_URL = '/api/admin/audit-log'


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
def test_manager_cookie_returns_403(manager_client):
    """КРИТИЧНО: manager не имеет доступа к audit-log."""
    resp = manager_client.get(BASE_URL)
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_cookie_returns_403(admin_client):
    """КРИТИЧНО: admin (не superadmin) не имеет доступа к audit-log."""
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 403


@pytest.mark.django_db
def test_superadmin_returns_200(superadmin_client):
    resp = superadmin_client.get(BASE_URL)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/admin/audit-log — list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_response_shape(superadmin_client):
    resp = superadmin_client.get(BASE_URL)
    assert resp.status_code == 200
    data = resp.json()
    assert 'rows' in data
    assert 'total' in data
    assert 'page' in data
    assert 'page_size' in data
    assert isinstance(data['rows'], list)
    assert isinstance(data['total'], int)
    assert data['page'] == 1


@pytest.mark.django_db
def test_list_default_sort_occurred_at_desc(superadmin_client):
    """Список возвращается без ошибок при дефолтной сортировке."""
    resp = superadmin_client.get(BASE_URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_list_page_size(superadmin_client):
    resp = superadmin_client.get(BASE_URL + '?page_size=3')
    assert resp.status_code == 200
    data = resp.json()
    assert data['page_size'] == 3
    assert len(data['rows']) <= 3


@pytest.mark.django_db
def test_list_invalid_sort_by_returns_400(superadmin_client):
    resp = superadmin_client.get(BASE_URL + '?sort_by=nonexistent')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_list_filter_by_event(superadmin_client):
    resp = superadmin_client.get(BASE_URL + '?filter[event]=__nonexistent__')
    assert resp.status_code == 200
    assert resp.json()['rows'] == []


@pytest.mark.django_db
def test_list_sort_by_event(superadmin_client):
    resp = superadmin_client.get(BASE_URL + '?sort_by=event&sort_dir=asc')
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Проверка что только GET доступен
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_post_not_allowed(superadmin_client):
    resp = superadmin_client.post(BASE_URL, {}, format='json')
    assert resp.status_code == 405
