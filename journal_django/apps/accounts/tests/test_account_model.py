"""Юнит-тесты модели Account: роль superadmin, full_name, is_superadmin."""
from __future__ import annotations

import pytest

from apps.accounts.models import Account

pytestmark = pytest.mark.django_db


def test_superadmin_role_choice_exists():
    assert Account.Role.SUPERADMIN == 'superadmin'
    assert ('superadmin', 'Суперадминистратор') in Account.Role.choices


def test_is_superadmin_property():
    acc = Account(email='s@example.com', role='superadmin')
    assert acc.is_superadmin is True
    assert acc.is_admin is False
    assert acc.has_role('superadmin') is True


def test_full_name_field_optional():
    acc = Account(email='m@example.com', role='manager', full_name='Иван Петров')
    assert acc.full_name == 'Иван Петров'
    acc2 = Account(email='m2@example.com', role='manager')
    assert acc2.full_name in (None, '')
