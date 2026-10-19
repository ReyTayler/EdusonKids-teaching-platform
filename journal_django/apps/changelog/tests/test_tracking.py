"""
Тесты захвата изменений триггерами pghistory.

Ключевое: пути записи МИМО сигналов Django (queryset.update, bulk_create,
queryset.delete) обязаны попадать в журнал — ради этого выбран pghistory.
"""
from __future__ import annotations

import pytest
from django.apps import apps

from apps.directions.models import Direction

pytestmark = pytest.mark.django_db


def _event_model(app_label: str, model_name: str):
    return apps.get_model(app_label, model_name)


def _make_direction(name: str = '__chg_dir__') -> Direction:
    return Direction.objects.create(name=name)


def test_insert_captured():
    d = _make_direction()
    ev = _event_model('directions', 'DirectionEvent')
    events = ev.objects.filter(pgh_obj_id=d.id)
    assert events.count() == 1
    assert events.first().pgh_label == 'insert'


def test_save_update_captured():
    d = _make_direction()
    d.name = '__chg_dir_2__'
    d.save()
    ev = _event_model('directions', 'DirectionEvent')
    labels = list(ev.objects.filter(pgh_obj_id=d.id)
                  .order_by('pgh_id').values_list('pgh_label', flat=True))
    assert labels == ['insert', 'update']


def test_queryset_update_captured():
    """Soft-delete в проекте — .update(active=False): сигналы молчат, триггер обязан видеть."""
    d = _make_direction()
    Direction.objects.filter(id=d.id).update(active=False)
    ev = _event_model('directions', 'DirectionEvent')
    last = ev.objects.filter(pgh_obj_id=d.id).order_by('-pgh_id').first()
    assert last.pgh_label == 'update'
    assert last.active is False


def test_bulk_create_captured():
    Direction.objects.bulk_create([
        Direction(name='__chg_bulk_1__'),
        Direction(name='__chg_bulk_2__'),
    ])
    ev = _event_model('directions', 'DirectionEvent')
    assert ev.objects.filter(
        pgh_label='insert', name__startswith='__chg_bulk_'
    ).count() == 2


def test_account_secrets_not_tracked():
    """У AccountEvent нет колонок секретов и технического шума."""
    ev = _event_model('accounts', 'AccountEvent')
    field_names = {f.name for f in ev._meta.get_fields()}
    for forbidden in ('password', 'twofa_secret', 'token_version',
                      'last_login', 'failed_login_count', 'locked_until'):
        assert forbidden not in field_names
    assert 'email' in field_names
    assert 'role' in field_names


def test_delete_captured_and_snapshot_kept():
    d = _make_direction()
    d_id = d.id
    Direction.objects.filter(id=d_id).delete()
    ev = _event_model('directions', 'DirectionEvent')
    last = ev.objects.filter(pgh_obj_id=d_id).order_by('-pgh_id').first()
    assert last.pgh_label == 'delete'
    # Снапшот пережил удаление строки (FK без constraint)
    assert last.name == '__chg_dir__'
