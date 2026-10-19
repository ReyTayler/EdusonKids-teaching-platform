"""
GET /api/admin/changelog — лента операций (1 строка = 1 контекст).
Контракт пагинации проекта: { rows, total, page, page_size }.
RBAC: просмотр — admin/superadmin; manager/teacher/anon — без доступа.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _mutate(client, name='__chg_list_dir__'):
    # Запись направлений — только superadmin (ReadStaffWriteSuperAdmin), поэтому
    # генератор событий в этих тестах — superadmin_client.
    resp = client.post('/api/admin/directions', {
        'name': name,
    }, format='json')
    assert resp.status_code in (200, 201), resp.content
    return resp


def test_rbac_view(admin_client, manager_client, superadmin_client, teacher_client, anon_client):
    """Просмотр ленты — admin/superadmin; manager/teacher/anon — без доступа
    (журнал изменений закрыт для manager)."""
    assert admin_client.get('/api/admin/changelog').status_code == 200
    assert superadmin_client.get('/api/admin/changelog').status_code == 200
    assert manager_client.get('/api/admin/changelog').status_code == 403
    assert teacher_client.get('/api/admin/changelog').status_code == 403
    assert anon_client.get('/api/admin/changelog').status_code in (401, 403)


def test_feed_row_shape(superadmin_client):
    _mutate(superadmin_client)
    data = superadmin_client.get('/api/admin/changelog').json()
    assert set(data) == {'rows', 'total', 'page', 'page_size'}
    row = data['rows'][0]
    assert row['operation'] == 'direction.create'
    assert row['actor']['email'] == '__root_superadmin__@test.local'
    assert row['actor']['role'] == 'superadmin'
    assert row['actor']['name']  # имя актора (fallback — email)
    assert row['method'] == 'POST'
    assert row['url'] == '/api/admin/directions'
    assert row['events_total'] == 1
    assert row['entities'] == [{'entity': 'direction', 'inserts': 1, 'updates': 0, 'deletes': 0}]
    assert row['revertable'] is True
    assert row['reverted'] is False
    assert '__chg_list_dir__' in row['summary']  # человекочитаемое описание
    assert 'occurred_at' in row and 'id' in row


def test_feed_reverted_status(superadmin_client):
    """После отката исходная операция помечается reverted и теряет revertable."""
    _mutate(superadmin_client, name='__chg_revstat__')
    op_id = superadmin_client.get('/api/admin/changelog?page_size=1').json()['rows'][0]['id']
    assert superadmin_client.post(f'/api/admin/changelog/{op_id}/revert').status_code == 200

    rows = superadmin_client.get('/api/admin/changelog?page_size=5').json()['rows']
    original = next(r for r in rows if r['id'] == op_id)
    assert original['reverted'] is True
    assert original['revertable'] is False
    revert_row = next(r for r in rows if r['operation'] == 'changelog.revert')
    assert 'Откат' in revert_row['summary']


def test_filter_by_actor_and_operation(superadmin_client):
    _mutate(superadmin_client)
    ok = superadmin_client.get(
        '/api/admin/changelog?filter[actor]=root_superadmin&filter[operation]=direction.create'
    ).json()
    assert ok['total'] >= 1
    miss = superadmin_client.get('/api/admin/changelog?filter[actor]=nobody@x').json()
    assert miss['total'] == 0
    miss2 = superadmin_client.get('/api/admin/changelog?filter[operation]=group.delete').json()
    assert miss2['total'] == 0


def test_filter_by_entity(superadmin_client):
    resp = _mutate(superadmin_client)
    direction_id = resp.json()['id']
    found = superadmin_client.get(
        f'/api/admin/changelog?filter[entity]=direction&filter[entity_id]={direction_id}'
    ).json()
    assert found['total'] == 1
    empty = superadmin_client.get('/api/admin/changelog?filter[entity]=group').json()
    assert empty['total'] == 0


def test_pagination(superadmin_client):
    for i in range(3):
        _mutate(superadmin_client, name=f'__chg_pg_{i}__')
    page = superadmin_client.get('/api/admin/changelog?page=2&page_size=1').json()
    assert page['page'] == 2 and page['page_size'] == 1
    assert len(page['rows']) == 1
    assert page['total'] >= 3
