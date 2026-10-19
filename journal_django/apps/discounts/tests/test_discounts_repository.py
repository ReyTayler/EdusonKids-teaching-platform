"""
Unit-тесты для DiscountsRepository.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.discounts import repository


def _cleanup_discount(discount_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM discounts WHERE id = %s', [discount_id])


@pytest.mark.django_db
def test_list_discounts_returns_list():
    result = repository.list_discounts()
    assert isinstance(result, list)


@pytest.mark.django_db
def test_list_discounts_active_only():
    result = repository.list_discounts(include_inactive=False)
    for row in result:
        assert row['active'] is True


@pytest.mark.django_db
def test_get_discount_nonexistent_returns_none():
    result = repository.get_discount(999999999)
    assert result is None


@pytest.mark.django_db
def test_create_and_get_discount():
    d = repository.create_discount({'name': '__test_repo_discount__', 'amount': Decimal('0.1')})
    assert d is not None
    did = d['id']
    try:
        fetched = repository.get_discount(did)
        assert fetched is not None
        assert fetched['name'] == '__test_repo_discount__'
        assert float(fetched['amount']) == pytest.approx(0.1)
        assert fetched['active'] is True
    finally:
        _cleanup_discount(did)


@pytest.mark.django_db
def test_update_discount_name():
    d = repository.create_discount({'name': '__test_repo_disc_upd__', 'amount': Decimal('0.05')})
    did = d['id']
    try:
        updated = repository.update_discount(did, {'name': '__test_repo_disc_upd2__'})
        assert updated is not None
        assert updated['name'] == '__test_repo_disc_upd2__'
    finally:
        _cleanup_discount(did)


@pytest.mark.django_db
def test_update_discount_active_false():
    d = repository.create_discount({'name': '__test_repo_disc_deact__', 'amount': Decimal('0.2')})
    did = d['id']
    try:
        updated = repository.update_discount(did, {'active': False})
        assert updated is not None
        assert updated['active'] is False
    finally:
        _cleanup_discount(did)


@pytest.mark.django_db
def test_update_discount_nonexistent_returns_none():
    result = repository.update_discount(999999999, {'name': 'ghost'})
    assert result is None


@pytest.mark.django_db
def test_soft_delete_discount():
    d = repository.create_discount({'name': '__test_repo_disc_del__', 'amount': Decimal('0.15')})
    did = d['id']
    try:
        ok = repository.soft_delete_discount(did)
        assert ok is True
        fetched = repository.get_discount(did)
        assert fetched['active'] is False
    finally:
        _cleanup_discount(did)


@pytest.mark.django_db
def test_soft_delete_discount_nonexistent():
    ok = repository.soft_delete_discount(999999999)
    assert ok is False
