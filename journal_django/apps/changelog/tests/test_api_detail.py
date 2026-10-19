"""GET /api/admin/changelog/<uuid> — события операции с diff «было/стало»."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _create_and_rename(client):
    # Запись направлений — только superadmin (ReadStaffWriteSuperAdmin),
    # поэтому генератор событий здесь — superadmin_client.
    resp = client.post('/api/admin/directions', {
        'name': '__chg_det_1__',
    }, format='json')
    direction_id = resp.json()['id']
    client.patch(f'/api/admin/directions/{direction_id}',
                 {'name': '__chg_det_2__'}, format='json')
    feed = client.get('/api/admin/changelog?page_size=2').json()['rows']
    update_op = next(r for r in feed if r['operation'] == 'direction.update')
    return direction_id, update_op['id']


def test_detail_diff(superadmin_client):
    direction_id, ctx_id = _create_and_rename(superadmin_client)
    data = superadmin_client.get(f'/api/admin/changelog/{ctx_id}').json()
    assert data['operation'] == 'direction.update'
    assert data['revertable'] is True
    assert data['actor']['role'] == 'superadmin'
    assert len(data['events']) == 1
    ev = data['events'][0]
    assert ev['entity'] == 'direction'
    assert int(ev['obj_id']) == direction_id
    assert ev['label'] == 'update'
    assert ev['diff']['name'] == ['__chg_det_1__', '__chg_det_2__']
    # у каждого события есть непустое человекочитаемое описание
    assert all(e.get('description', '').strip() for e in data['events'])
    assert 'название' in ev['description']  # русская подпись поля
    # human-объект: заголовок, фраза и очеловеченный список изменений
    human = ev['human']
    assert human['title'].strip() and human['text'].strip()
    name_change = next(c for c in human['changes'] if c['label'] == 'название')
    assert name_change['old'] == '__chg_det_1__'
    assert name_change['new'] == '__chg_det_2__'
    # доступная к откату операция → причина null
    assert data['not_revertable_reason'] is None


def test_detail_revert_not_revertable(superadmin_client):
    """Детали самой revert-операции: revertable=False (откат отката запрещён)."""
    resp = superadmin_client.post('/api/admin/directions', {
        'name': '__chg_det_rev__',
    }, format='json')
    assert resp.status_code in (200, 201)
    op_id = superadmin_client.get('/api/admin/changelog?page_size=1').json()['rows'][0]['id']
    assert superadmin_client.post(f'/api/admin/changelog/{op_id}/revert').status_code == 200
    rows = superadmin_client.get('/api/admin/changelog?page_size=5').json()['rows']
    revert_id = next(r['id'] for r in rows if r['operation'] == 'changelog.revert')
    data = superadmin_client.get(f'/api/admin/changelog/{revert_id}').json()
    assert data['operation'] == 'changelog.revert'
    assert data['revertable'] is False
    assert data['not_revertable_reason'] == 'операции отката не откатываются'


def test_detail_404(admin_client):
    # Просмотр деталей разрешён admin (IsAdminOrSuperAdmin) — несуществующий uuid → 404.
    resp = admin_client.get('/api/admin/changelog/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 404


def test_detail_rbac_manager_forbidden(manager_client):
    """Журнал изменений закрыт для manager — 403 (даже на несуществующий uuid)."""
    resp = manager_client.get('/api/admin/changelog/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 403


def test_detail_rbac_teacher_and_anon_forbidden(teacher_client, anon_client):
    """Teacher/anon по-прежнему без доступа к деталям."""
    url = '/api/admin/changelog/00000000-0000-0000-0000-000000000000'
    assert teacher_client.get(url).status_code == 403
    assert anon_client.get(url).status_code in (401, 403)
