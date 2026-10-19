"""E2E-тесты /api/admin/knowledge/sections."""
from __future__ import annotations

import pytest

from apps.knowledge.tests.conftest import cleanup_kb

BASE_URL = '/api/admin/knowledge/sections'


@pytest.fixture(autouse=True)
def _clean():
    cleanup_kb()
    yield
    cleanup_kb()


# --- доступ ---------------------------------------------------------------

@pytest.mark.django_db
def test_anonymous_gets_401(anon_client):
    assert anon_client.get(BASE_URL).status_code == 401


@pytest.mark.django_db
def test_teacher_can_read(teacher_client):
    assert teacher_client.get(BASE_URL).status_code == 200


@pytest.mark.django_db
def test_manager_cannot_create(manager_client):
    resp = manager_client.post(BASE_URL, {'title': '__test_kb_x'}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_can_create(admin_client):
    resp = admin_client.post(BASE_URL, {'title': '__test_kb_методика'}, format='json')
    assert resp.status_code == 201
    body = resp.json()
    assert body['title'] == '__test_kb_методика'
    assert body['active'] is True


# --- CRUD ------------------------------------------------------------------

@pytest.mark.django_db
def test_duplicate_title_returns_409(admin_client):
    admin_client.post(BASE_URL, {'title': '__test_kb_dup'}, format='json')
    resp = admin_client.post(BASE_URL, {'title': '__test_kb_dup'}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_patch_renames(admin_client):
    created = admin_client.post(BASE_URL, {'title': '__test_kb_a'}, format='json').json()
    resp = admin_client.patch(
        f"{BASE_URL}/{created['id']}", {'title': '__test_kb_b'}, format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['title'] == '__test_kb_b'


@pytest.mark.django_db
def test_delete_empty_section_succeeds(admin_client):
    created = admin_client.post(BASE_URL, {'title': '__test_kb_del'}, format='json').json()
    assert admin_client.delete(f"{BASE_URL}/{created['id']}").status_code == 204


@pytest.mark.django_db
def test_delete_section_with_documents_returns_409(admin_client):
    section = admin_client.post(BASE_URL, {'title': '__test_kb_busy'}, format='json').json()
    admin_client.post(
        '/api/admin/knowledge/documents',
        {'section_id': section['id'], 'title': '__test_kb_doc'},
        format='json',
    )
    resp = admin_client.delete(f"{BASE_URL}/{section['id']}")
    assert resp.status_code == 409
    assert resp.json()['error'] == 'has_documents'


@pytest.mark.django_db
def test_reorder_sets_positions(admin_client):
    first = admin_client.post(BASE_URL, {'title': '__test_kb_1'}, format='json').json()
    second = admin_client.post(BASE_URL, {'title': '__test_kb_2'}, format='json').json()
    resp = admin_client.post(
        f'{BASE_URL}/reorder', {'order': [second['id'], first['id']]}, format='json',
    )
    assert resp.status_code == 200
    titles = [s['title'] for s in admin_client.get(BASE_URL).json()['sections']
              if s['title'].startswith('__test_kb_')]
    assert titles == ['__test_kb_2', '__test_kb_1']
