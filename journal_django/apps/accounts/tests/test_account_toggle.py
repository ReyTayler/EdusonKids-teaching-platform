"""Отключение/включение учётки (обратимо)."""
from __future__ import annotations

import pytest

from apps.accounts import repository, services

pytestmark = pytest.mark.django_db


class _NoReq:
    META: dict = {}


def test_disable_then_enable():
    acc = repository.create_account(email='__tgl__@example.com', role='manager')
    assert services.set_active(acc['id'], False, actor_account_id=None, request=_NoReq()) is True
    assert repository.get_by_id(acc['id'])['is_active'] is False
    assert services.set_active(acc['id'], True, actor_account_id=None, request=_NoReq()) is True
    assert repository.get_by_id(acc['id'])['is_active'] is True


def test_set_active_missing_returns_false():
    assert services.set_active(999999, True, actor_account_id=None, request=_NoReq()) is False
