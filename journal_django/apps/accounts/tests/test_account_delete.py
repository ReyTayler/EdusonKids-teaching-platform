"""Hard-delete учётки: строка исчезает физически, инвайты/recovery каскадно удаляются."""
from __future__ import annotations

import pytest

from apps.accounts import repository, services
from apps.accounts.models import Account, AccountInvite

pytestmark = pytest.mark.django_db


class _NoReq:
    META: dict = {}


def test_hard_delete_removes_row():
    acc = repository.create_account(email='__del__@example.com', role='manager')
    assert services.hard_delete(acc['id'], actor_account_id=None, request=_NoReq()) is True
    assert Account.objects.filter(id=acc['id']).exists() is False


def test_hard_delete_missing_returns_false():
    assert services.hard_delete(999999, actor_account_id=None, request=_NoReq()) is False


def test_hard_delete_cascades_invites():
    acc = repository.create_account(email='__del_inv__@example.com', role='manager')
    from django.utils import timezone
    import datetime
    AccountInvite.objects.create(
        account_id=acc['id'], token_hash='x' * 20,
        created_at=timezone.now(), expires_at=timezone.now() + datetime.timedelta(hours=1),
    )
    assert AccountInvite.objects.filter(account_id=acc['id']).exists() is True
    services.hard_delete(acc['id'], actor_account_id=None, request=_NoReq())
    assert AccountInvite.objects.filter(account_id=acc['id']).exists() is False
