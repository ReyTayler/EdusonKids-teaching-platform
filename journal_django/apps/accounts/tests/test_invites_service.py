"""
Тесты invite-сервиса (Task 2.4): issue_invite, _invite_url.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts import repository, services
from apps.core.utils.passwords import hash_invite_token

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Stub request (для log_event)
# ---------------------------------------------------------------------------

class _FakeRequest:
    META = {}


# ---------------------------------------------------------------------------
# issue_invite
# ---------------------------------------------------------------------------

def test_issue_invite_returns_url_and_expiry(account_factory):
    """issue_invite возвращает invite_url и expires_at для существующего аккаунта."""
    acc_id = account_factory(email='__svc_inv__@example.com', role='manager')
    result = services.issue_invite(acc_id, actor_account_id=acc_id, request=_FakeRequest())

    assert result is not None
    assert 'invite_url' in result
    assert 'expires_at' in result
    assert result['invite_url'].startswith('/login/set-password?token=')
    # expires_at ≈ сейчас + 48 часов (с небольшим допуском)
    expected = timezone.now() + datetime.timedelta(hours=48)
    delta = abs((result['expires_at'] - expected).total_seconds())
    assert delta < 10, f'expires_at слишком далеко от ожидаемого: delta={delta}s'


def test_issue_invite_token_not_stored_plaintext(account_factory):
    """Plaintext-токен НЕ хранится в БД — только SHA-256 хэш."""
    acc_id = account_factory(email='__svc_inv_plain__@example.com', role='manager')
    result = services.issue_invite(acc_id, actor_account_id=acc_id, request=_FakeRequest())

    # Извлечь plaintext из URL
    url = result['invite_url']
    plaintext = url.split('?token=', 1)[1]

    # В БД должен лежать хэш, а не plaintext
    with connection.cursor() as cur:
        cur.execute('SELECT token_hash FROM account_invites WHERE account_id = %s', [acc_id])
        rows = cur.fetchall()
    assert len(rows) >= 1
    stored_hashes = [r[0] for r in rows]
    # plaintext не хранится напрямую
    assert plaintext not in stored_hashes
    # но sha256(plaintext) — должен быть
    assert hash_invite_token(plaintext) in stored_hashes


def test_issue_invite_revokes_previous(account_factory):
    """Повторный вызов отзывает предыдущий активный инвайт."""
    acc_id = account_factory(email='__svc_inv_rev__@example.com', role='manager')
    first = services.issue_invite(acc_id, actor_account_id=acc_id, request=_FakeRequest())
    # Первый токен
    first_token = first['invite_url'].split('?token=', 1)[1]
    first_hash = hash_invite_token(first_token)

    second = services.issue_invite(acc_id, actor_account_id=acc_id, request=_FakeRequest())
    second_token = second['invite_url'].split('?token=', 1)[1]
    second_hash = hash_invite_token(second_token)

    # Первый инвайт теперь revoked
    assert repository.find_active_by_hash(first_hash) is None
    # Второй — активен
    assert repository.find_active_by_hash(second_hash) is not None


def test_issue_invite_logs_audit_event(account_factory):
    """issue_invite записывает audit-событие invite_created."""
    acc_id = account_factory(email='__svc_inv_audit__@example.com', role='manager')
    services.issue_invite(acc_id, actor_account_id=acc_id, request=_FakeRequest())

    with connection.cursor() as cur:
        cur.execute(
            "SELECT event FROM security_audit_log "
            "WHERE event = 'invite_created' AND target_id = %s",
            [acc_id],
        )
        row = cur.fetchone()
    assert row is not None, 'audit-событие invite_created не найдено'


def test_issue_invite_nonexistent_account():
    """Несуществующий аккаунт → None (без исключений)."""
    result = services.issue_invite(
        account_id=999_999_999,
        actor_account_id=1,
        request=_FakeRequest(),
    )
    assert result is None


# ---------------------------------------------------------------------------
# _invite_url
# ---------------------------------------------------------------------------

def test_invite_url_format():
    from apps.accounts.services import _invite_url
    url = _invite_url('abc123')
    assert url == '/login/set-password?token=abc123'
