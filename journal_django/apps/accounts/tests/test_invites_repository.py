"""
Интеграционные тесты invite-функций repository (Task 2.3).
Реальная БД, managed=True (таблица account_invites создана миграцией 0003).
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts import repository
from apps.core.utils.passwords import generate_invite_token, hash_invite_token

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Фикстура: аккаунт без пароля (password_hash nullable после 0003)
# ---------------------------------------------------------------------------

@pytest.fixture
def inv_account(account_factory):
    """Manager-аккаунт для invite-тестов."""
    return account_factory(email='__inv__@example.com', role='manager')


# ---------------------------------------------------------------------------
# create_invite
# ---------------------------------------------------------------------------

def test_create_invite_returns_row(inv_account):
    plaintext, token_hash = generate_invite_token()
    expires_at = timezone.now() + datetime.timedelta(hours=48)
    row = repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=expires_at,
    )
    assert row is not None
    assert row['token_hash'] == token_hash
    assert row['account_id'] == inv_account
    assert row['used_at'] is None
    assert row['revoked_at'] is None


# ---------------------------------------------------------------------------
# find_active_by_hash
# ---------------------------------------------------------------------------

def test_find_active_by_hash_found(inv_account):
    plaintext, token_hash = generate_invite_token()
    expires_at = timezone.now() + datetime.timedelta(hours=48)
    repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=expires_at,
    )
    found = repository.find_active_by_hash(token_hash)
    assert found is not None
    assert found['token_hash'] == token_hash


def test_find_active_by_hash_not_found():
    assert repository.find_active_by_hash('nonexistent_hash_xyz') is None


def test_find_active_by_hash_excludes_expired(inv_account):
    # Просроченный инвайт не должен считаться активным (иначе invite_lookup
    # ошибочно вернёт valid:true на «мёртвой» ссылке).
    plaintext, token_hash = generate_invite_token()
    repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=timezone.now() - datetime.timedelta(hours=1),
    )
    assert repository.find_active_by_hash(token_hash) is None


def test_find_active_by_hash_not_found_after_revoke(inv_account):
    plaintext, token_hash = generate_invite_token()
    expires_at = timezone.now() + datetime.timedelta(hours=48)
    repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=expires_at,
    )
    repository.revoke_active_for_account(inv_account)
    assert repository.find_active_by_hash(token_hash) is None


# ---------------------------------------------------------------------------
# revoke_active_for_account
# ---------------------------------------------------------------------------

def test_revoke_active_for_account(inv_account):
    plaintext, token_hash = generate_invite_token()
    expires_at = timezone.now() + datetime.timedelta(hours=48)
    repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=expires_at,
    )
    revoked = repository.revoke_active_for_account(inv_account)
    assert revoked == 1
    # Повторный отзыв — нечего отзывать
    assert repository.revoke_active_for_account(inv_account) == 0


def test_revoke_active_for_account_no_invites(inv_account):
    # Нет инвайтов — ничего не упало, вернуло 0
    assert repository.revoke_active_for_account(inv_account) == 0


# ---------------------------------------------------------------------------
# accept_invite
# ---------------------------------------------------------------------------

def test_accept_invite_atomically(inv_account):
    """accept_invite: отмечает used_at + ставит password_hash + бампит token_version."""
    plaintext, token_hash = generate_invite_token()
    expires_at = timezone.now() + datetime.timedelta(hours=48)
    invite = repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=expires_at,
    )
    invite_id = invite['id']

    before_state = repository.get_auth_state(inv_account)
    assert before_state['token_version'] == 0

    result = repository.accept_invite(invite_id, password_hash='$2b$12$newhash')
    assert result is not None
    assert result['used_at'] is not None

    # пароль установлен (колонка password после миграции на AbstractUser)
    acc = repository.get_by_id(inv_account)
    assert acc['password'] == '$2b$12$newhash'

    # token_version инкрементирован
    after_state = repository.get_auth_state(inv_account)
    assert after_state['token_version'] == 1


def test_accept_invite_idempotent_guard(inv_account):
    """Повторный accept уже использованного инвайта → None."""
    plaintext, token_hash = generate_invite_token()
    expires_at = timezone.now() + datetime.timedelta(hours=48)
    invite = repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=expires_at,
    )
    invite_id = invite['id']

    repository.accept_invite(invite_id, password_hash='$2b$12$first')
    # Повторный вызов — инвайт уже помечен used_at
    second = repository.accept_invite(invite_id, password_hash='$2b$12$second')
    assert second is None


def test_accept_invite_expired(inv_account):
    """Просроченный инвайт → None."""
    plaintext, token_hash = generate_invite_token()
    # expires_at в прошлом
    expires_at = timezone.now() - datetime.timedelta(hours=1)
    invite = repository.create_invite(
        account_id=inv_account,
        token_hash=token_hash,
        created_by=inv_account,
        expires_at=expires_at,
    )
    result = repository.accept_invite(invite['id'], password_hash='$2b$12$x')
    assert result is None


def test_accept_invite_nonexistent():
    """Несуществующий invite_id → None."""
    result = repository.accept_invite(999_999_999, password_hash='$2b$12$x')
    assert result is None
