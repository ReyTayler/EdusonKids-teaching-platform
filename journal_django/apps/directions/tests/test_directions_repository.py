"""
Unit-тесты для DirectionsRepository.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.directions import repository


def _cleanup_direction(direction_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


def _direction_data(**overrides) -> dict:
    return {
        'name': '__test_direction__',
        **overrides,
    }


@pytest.mark.django_db
def test_list_directions_returns_list():
    result = repository.list_directions()
    assert isinstance(result, list)


@pytest.mark.django_db
def test_list_directions_active_only():
    result = repository.list_directions(include_inactive=False)
    for row in result:
        assert row['active'] is True


@pytest.mark.django_db
def test_get_direction_nonexistent_returns_none():
    result = repository.get_direction(999999999)
    assert result is None


@pytest.mark.django_db
def test_create_and_get_direction():
    d = repository.create_direction(_direction_data(name='__test_repo_dir__'))
    assert d is not None
    did = d['id']
    try:
        fetched = repository.get_direction(did)
        assert fetched is not None
        assert fetched['name'] == '__test_repo_dir__'
        assert fetched['active'] is True
    finally:
        _cleanup_direction(did)


@pytest.mark.django_db
def test_create_direction_with_optional_fields():
    d = repository.create_direction(_direction_data(
        name='__test_repo_dir_opt__',
        total_lessons=8,
        color='#FF0000',
        subscription_price=1200,
    ))
    did = d['id']
    try:
        fetched = repository.get_direction(did)
        assert fetched['total_lessons'] == 8
        assert fetched['color'] == '#FF0000'
    finally:
        _cleanup_direction(did)


@pytest.mark.django_db
def test_update_direction_name():
    d = repository.create_direction(_direction_data(name='__test_repo_dir_upd__'))
    did = d['id']
    try:
        updated = repository.update_direction(did, {'name': '__test_repo_dir_upd2__'})
        assert updated is not None
        assert updated['name'] == '__test_repo_dir_upd2__'
    finally:
        _cleanup_direction(did)


@pytest.mark.django_db
def test_update_direction_subscription_price():
    d = repository.create_direction(_direction_data(name='__test_repo_dir_sp__'))
    did = d['id']
    try:
        updated = repository.update_direction(did, {'subscription_price': 999})
        assert updated is not None
        # Значение может быть Decimal или число — сравниваем как float
        assert float(updated['subscription_price']) == 999.0
    finally:
        _cleanup_direction(did)


@pytest.mark.django_db
def test_update_direction_nonexistent_returns_none():
    result = repository.update_direction(999999999, {'name': 'ghost'})
    assert result is None


@pytest.mark.django_db
def test_soft_delete_direction():
    d = repository.create_direction(_direction_data(name='__test_repo_dir_del__'))
    did = d['id']
    try:
        ok = repository.soft_delete_direction(did)
        assert ok is True
        fetched = repository.get_direction(did)
        assert fetched['active'] is False
    finally:
        _cleanup_direction(did)


@pytest.mark.django_db
def test_soft_delete_direction_nonexistent():
    ok = repository.soft_delete_direction(999999999)
    assert ok is False


@pytest.mark.django_db
def test_get_direction_payments_count_returns_int():
    # Проверяем что функция возвращает int (может быть 0 для несуществующего)
    count = repository.get_direction_payments_count(999999999)
    assert isinstance(count, int)
    assert count == 0
