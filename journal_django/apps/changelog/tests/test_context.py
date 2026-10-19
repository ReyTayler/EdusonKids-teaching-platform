"""
Контекст журнала: middleware даёт url/method, CookieJWTAuthentication — актора.
Проверяем через реальный API-вызов (admin_client из conftest).
"""
from __future__ import annotations

import pytest
from django.apps import apps

pytestmark = pytest.mark.django_db


def test_api_mutation_has_actor_and_url(superadmin_client):
    # Запись направлений — только superadmin (ReadStaffWriteSuperAdmin), поэтому
    # актор события здесь superadmin, а не admin.
    resp = superadmin_client.post('/api/admin/directions', {
        'name': '__chg_ctx_dir__',
    }, format='json')
    assert resp.status_code in (200, 201), resp.content

    ev_model = apps.get_model('directions', 'DirectionEvent')
    ev = ev_model.objects.filter(name='__chg_ctx_dir__').order_by('-pgh_id').first()
    assert ev is not None
    assert ev.pgh_context is not None
    meta = ev.pgh_context.metadata
    assert meta['url'] == '/api/admin/directions'
    assert meta['method'] == 'POST'
    assert meta['email'] == '__root_superadmin__@test.local'
    assert meta['role'] == 'superadmin'
    assert isinstance(meta['account_id'], int)


def test_orm_write_without_request_has_no_context():
    from apps.directions.models import Direction
    d = Direction.objects.create(name='__chg_noctx__')
    ev_model = apps.get_model('directions', 'DirectionEvent')
    ev = ev_model.objects.filter(pgh_obj_id=d.id).first()
    assert ev.pgh_context_id is None
