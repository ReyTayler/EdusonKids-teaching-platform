"""
Integration-тесты repository слоя accounts (реальная БД, managed=False).
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.accounts import repository

pytestmark = pytest.mark.django_db


def test_list_accounts_envelope_no_secrets(account_factory):
    account_factory(email='__acc_list__@example.com', role='manager')
    result = repository.list_accounts(filters={'email': '__acc_list__'})
    assert set(result.keys()) == {'rows', 'total', 'page', 'page_size'}
    assert result['total'] == 1
    row = result['rows'][0]
    # SELECT не содержит секретов (после миграции колонка называется password)
    assert 'password' not in row
    assert 'twofa_secret' not in row
    assert row['email'] == '__acc_list__@example.com'


def test_find_by_email(account_factory):
    account_factory(email='__acc_find__@example.com')
    found = repository.find_by_email('__acc_find__@example.com')
    assert found is not None
    assert found['email'] == '__acc_find__@example.com'
    assert repository.find_by_email('__nope__@example.com') is None


def test_get_by_id_with_teacher(teacher_fixture, account_factory):
    acc_id = account_factory(email='__acc_t__@example.com', role='teacher', teacher_id=teacher_fixture)
    row = repository.get_by_id_with_teacher(acc_id)
    assert row['teacher_name'] == '__acc_teacher__'
    assert row['teacher_id'] == teacher_fixture


def test_create_account_returns_full_row(cleanup_email):
    cleanup_email.append('__acc_new__@example.com')
    acc = repository.create_account(
        email='__acc_new__@example.com', password='$2b$12$x', role='manager',
    )
    assert acc['email'] == '__acc_new__@example.com'
    assert acc['role'] == 'manager'
    assert acc['is_active'] is True


def test_update_account_coalesce(account_factory):
    acc_id = account_factory(email='__acc_upd__@example.com', role='manager')
    updated = repository.update_account(acc_id, active=False)
    assert updated['is_active'] is False
    assert updated['email'] == '__acc_upd__@example.com'  # не затёрто
    assert updated['role'] == 'manager'


def test_update_account_missing_none():
    assert repository.update_account(999_999_999, active=False) is None


def test_set_password(account_factory):
    acc_id = account_factory(email='__acc_pwd__@example.com')
    assert repository.set_password(acc_id, '$2b$12$newhash') is True
    assert repository.set_password(999_999_999, '$2b$12$x') is False
    row = repository.get_by_id(acc_id)
    assert row['password'] == '$2b$12$newhash'


def test_soft_delete(account_factory):
    acc_id = account_factory(email='__acc_del__@example.com')
    assert repository.soft_delete(acc_id) is True
    assert repository.get_by_id(acc_id)['is_active'] is False
    assert repository.soft_delete(999_999_999) is False


def test_reset_twofa_clears_fields_and_codes(account_factory):
    acc_id = account_factory(email='__acc_2fa__@example.com', twofa=True)
    # Перед сбросом есть recovery-код
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM account_recovery_codes WHERE account_id = %s', [acc_id])
        assert cur.fetchone()[0] == 1

    row = repository.reset_twofa(acc_id)
    assert row['twofa_method'] is None
    assert row['twofa_secret'] is None
    assert row['twofa_enabled'] is False
    assert row['twofa_confirmed_at'] is None
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM account_recovery_codes WHERE account_id = %s', [acc_id])
        assert cur.fetchone()[0] == 0


def test_reset_twofa_missing_none():
    assert repository.reset_twofa(999_999_999) is None


def test_bump_token_version_increments(account_factory):
    from apps.accounts import repository
    acc_id = account_factory(email='__tv__@example.com', role='manager')
    before = repository.get_auth_state(acc_id)
    assert before['token_version'] == 0
    assert before['is_active'] is True
    repository.bump_token_version(acc_id)
    after = repository.get_auth_state(acc_id)
    assert after['token_version'] == 1


def test_get_auth_state_missing_returns_none():
    from apps.accounts import repository
    assert repository.get_auth_state(99_999_999) is None
