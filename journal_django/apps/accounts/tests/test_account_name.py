"""Имя учётки — производное: full_name or teacher_name or email."""
from __future__ import annotations

import pytest

from apps.accounts import repository
from apps.accounts.models import Account

pytestmark = pytest.mark.django_db


def test_list_returns_name_from_full_name():
    acc = repository.create_account(email='__nm_mgr__@example.com', role='manager')
    repository.update_full_name(acc['id'], 'Пётр Иванов')
    rows = repository.list_accounts(filters={'email': '__nm_mgr__'})['rows']
    row = next(r for r in rows if r['email'] == '__nm_mgr__@example.com')
    assert row['name'] == 'Пётр Иванов'
    Account.objects.filter(id=acc['id']).delete()


def test_list_falls_back_to_email_without_full_name():
    acc = repository.create_account(email='__nm_nofull__@example.com', role='manager')
    rows = repository.list_accounts(filters={'email': '__nm_nofull__'})['rows']
    row = next(r for r in rows if r['email'] == '__nm_nofull__@example.com')
    assert row['name'] == '__nm_nofull__@example.com'
    Account.objects.filter(id=acc['id']).delete()


def test_get_account_returns_derived_name():
    from apps.accounts import services
    acc = repository.create_account(email='__nm_get__@example.com', role='admin', full_name='Мария Смирнова')
    row = services.get_account(acc['id'])
    assert row['name'] == 'Мария Смирнова'
    Account.objects.filter(id=acc['id']).delete()
