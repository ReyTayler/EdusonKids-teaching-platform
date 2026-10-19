"""
test_invite_flow.py — TDD-тесты для ШАГ 3.1 + 3.2 (invite_lookup / invite_accept).

Покрывает:
  - invite_lookup: валидный токен → {valid:True, email, role}
  - invite_lookup: невалидный/просроченный/использованный → ровно {valid:False} (анти-энумерация)
  - invite_accept: ставит пароль, возвращает enroll-challenge, account=None (нет сессии)
  - invite_accept: двойной accept → 400
  - API GET /api/auth/invite?token=… → 200 valid:true
  - API POST /api/auth/invite/accept → 200 challenge_token, сессия НЕ в cookies
"""
from __future__ import annotations

import datetime

import pytest
from django.contrib.auth.hashers import check_password
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.utils.passwords import generate_invite_token, hash_invite_token

pytestmark = pytest.mark.django_db

BASE_AUTH = '/api/auth'
_PASSWORD = 'password123'


@pytest.fixture
def invite_factory(account_factory):
    """
    Создать аккаунт + invite-запись. Возвращает (plaintext_token, account).
    Очистка инвайтов входит в account_factory (DELETE account_invites WHERE account_id).
    """
    def factory(
        email='__inv__@example.com',
        role='manager',
        expired=False,
        used=False,
    ):
        acc = account_factory(email=email, role=role, password='')
        plaintext, token_hash = generate_invite_token()
        if expired:
            expires_at = timezone.now() - datetime.timedelta(hours=1)
        else:
            expires_at = timezone.now() + datetime.timedelta(hours=48)

        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO account_invites '
                '(account_id, token_hash, created_by, created_at, expires_at) '
                'VALUES (%s, %s, %s, now(), %s) RETURNING id',
                [acc['id'], token_hash, acc['id'], expires_at],
            )
            invite_id = cur.fetchone()[0]
            if used:
                cur.execute(
                    'UPDATE account_invites SET used_at=now() WHERE id=%s',
                    [invite_id],
                )
        return plaintext, acc

    return factory


# ---------------------------------------------------------------------------
# 3.1 — service invite_lookup
# ---------------------------------------------------------------------------

class TestInviteLookup:
    """Сервисный слой invite_lookup."""

    def test_lookup_valid_returns_email_and_role(self, invite_factory):
        """Валидный токен → valid=True + email + role."""
        from apps.auth_app.services import invite_lookup
        token, acc = invite_factory(email='__lu_ok__@example.com', role='manager')
        data, status = invite_lookup(token)
        assert status == 200
        assert data == {'valid': True, 'email': acc['email'], 'role': acc['role']}

    def test_lookup_invalid_token_returns_valid_false_only(self):
        """Невалидный токен → ровно {valid: False}, без email/role (анти-энумерация)."""
        from apps.auth_app.services import invite_lookup
        data, status = invite_lookup('nonexistent_token')
        assert status == 200
        assert data == {'valid': False}
        assert 'email' not in data
        assert 'role' not in data

    def test_lookup_expired_returns_valid_false_only(self, invite_factory):
        """Просроченный invite → {valid: False}."""
        from apps.auth_app.services import invite_lookup
        token, _ = invite_factory(email='__lu_exp__@example.com', expired=True)
        data, status = invite_lookup(token)
        assert status == 200
        assert data == {'valid': False}

    def test_lookup_used_returns_valid_false_only(self, invite_factory):
        """Использованный invite → {valid: False}."""
        from apps.auth_app.services import invite_lookup
        token, _ = invite_factory(email='__lu_used__@example.com', used=True)
        data, status = invite_lookup(token)
        assert status == 200
        assert data == {'valid': False}

    def test_lookup_empty_token_returns_valid_false(self):
        """Пустой токен → {valid: False}."""
        from apps.auth_app.services import invite_lookup
        data, status = invite_lookup('')
        assert status == 200
        assert data == {'valid': False}


# ---------------------------------------------------------------------------
# 3.1 — service invite_accept
# ---------------------------------------------------------------------------

class TestInviteAccept:
    """Сервисный слой invite_accept."""

    def test_accept_valid_sets_password_and_returns_challenge(self, invite_factory):
        """Валидный invite + пароль → challenge_token (enroll), account=None."""
        from apps.auth_app.services import invite_accept
        token, acc = invite_factory(email='__ia_ok__@example.com', role='manager')
        data, status, account = invite_accept(token, _PASSWORD)
        assert status == 200
        assert 'challenge_token' in data
        assert account is None  # сессия НЕ выдаётся

    def test_accept_sets_password_in_db(self, invite_factory):
        """После accept пароль реально устанавливается в БД (Django hasher)."""
        from apps.auth_app.services import invite_accept
        token, acc = invite_factory(email='__ia_pw__@example.com', role='manager')
        invite_accept(token, _PASSWORD)
        with connection.cursor() as cur:
            cur.execute('SELECT password FROM accounts WHERE id=%s', [acc['id']])
            stored_hash = cur.fetchone()[0]
        assert stored_hash is not None
        assert check_password(_PASSWORD, stored_hash)

    def test_accept_invalid_token_returns_400(self):
        """Невалидный токен → 400, единый ответ (анти-энумерация)."""
        from apps.auth_app.services import invite_accept
        data, status, account = invite_accept('bad_token', _PASSWORD)
        assert status == 400
        assert 'error' in data
        assert account is None

    def test_accept_double_use_returns_400(self, invite_factory):
        """Второй accept того же токена → 400 (используемый инвайт)."""
        from apps.auth_app.services import invite_accept
        token, acc = invite_factory(email='__ia_dbl__@example.com', role='manager')
        # Первый accept — успех
        data1, status1, _ = invite_accept(token, _PASSWORD)
        assert status1 == 200
        # Второй accept — провал
        data2, status2, _ = invite_accept(token, _PASSWORD)
        assert status2 == 400
        assert 'error' in data2

    def test_accept_returns_enroll_stage_challenge(self, invite_factory):
        """challenge_token декодируется как stage=enroll."""
        from apps.auth_app.services import invite_accept, read_challenge
        token, acc = invite_factory(email='__ia_stage__@example.com', role='manager')
        data, status, _ = invite_accept(token, _PASSWORD)
        assert status == 200
        ch = read_challenge(data['challenge_token'])
        assert ch is not None
        assert ch['stage'] == 'enroll'
        assert ch['account_id'] == acc['id']

    def test_accept_with_existing_totp_returns_verify(self, invite_factory):
        """Аккаунт с УЖЕ настроенной TOTP-2FA (сброс пароля): accept → verify по
        настроенному методу (twofa_required), БЕЗ повторного enrollment."""
        from apps.auth_app.services import invite_accept, read_challenge
        token, acc = invite_factory(email='__ia_2fat__@example.com', role='manager')
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET twofa_enabled=true, twofa_method='totp', "
                "twofa_secret='SEED' WHERE id=%s", [acc['id']],
            )
        data, status, account = invite_accept(token, _PASSWORD)
        assert status == 200
        assert account is None
        assert data.get('twofa_required') is True
        assert data.get('method') == 'totp'
        assert 'twofa_enrollment_required' not in data
        # challenge — verify, НЕ enroll
        ch = read_challenge(data['challenge_token'])
        assert ch['stage'] == 'verify'

    def test_accept_with_existing_email_2fa_sends_code(self, invite_factory):
        """Аккаунт с email-2FA: accept → twofa_required method=email + код отправлен."""
        from unittest.mock import patch
        from apps.auth_app.services import invite_accept
        token, acc = invite_factory(email='__ia_2fae__@example.com', role='manager')
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET twofa_enabled=true, twofa_method='email' WHERE id=%s",
                [acc['id']],
            )
        with patch('apps.auth_app.services.send_otp_email') as mock_send:
            data, status, account = invite_accept(token, _PASSWORD)
        assert status == 200
        assert data.get('twofa_required') is True
        assert data.get('method') == 'email'
        mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 3.2 — API tests
# ---------------------------------------------------------------------------

class TestInviteAPI:
    """HTTP-уровень: GET /api/auth/invite и POST /api/auth/invite/accept."""

    def test_get_invite_valid_returns_200_valid_true(self, invite_factory):
        """GET /api/auth/invite?token=… → 200, valid:True для активного инвайта."""
        token, acc = invite_factory(email='__api_inv_ok__@example.com', role='manager')
        resp = APIClient().get(f'{BASE_AUTH}/invite', {'token': token})
        assert resp.status_code == 200
        assert resp.data.get('valid') is True
        assert resp.data.get('email') == acc['email']
        assert resp.data.get('role') == acc['role']

    def test_get_invite_invalid_returns_200_valid_false(self):
        """GET /api/auth/invite?token=bad → 200, valid:False."""
        resp = APIClient().get(f'{BASE_AUTH}/invite', {'token': 'bad_token'})
        assert resp.status_code == 200
        assert resp.data == {'valid': False}

    def test_get_invite_no_token_returns_valid_false(self):
        """GET /api/auth/invite без token → 200, valid:False."""
        resp = APIClient().get(f'{BASE_AUTH}/invite')
        assert resp.status_code == 200
        assert resp.data == {'valid': False}

    def test_post_invite_accept_success_no_session_cookie(self, invite_factory):
        """POST /api/auth/invite/accept → 200 challenge_token, session НЕ в cookies.

        Фаза 4: cookie 'access' (JWT) тоже НЕ должна устанавливаться — пользователь
        не аутентифицирован до завершения 2FA-enroll.
        """
        from django.conf import settings as dj_settings
        token, acc = invite_factory(email='__api_acc_ok__@example.com', role='manager')
        resp = APIClient().post(f'{BASE_AUTH}/invite/accept', {
            'token': token,
            'password': _PASSWORD,
        }, format='json')
        assert resp.status_code == 200
        assert 'challenge_token' in resp.data
        # Ключевой инвариант: сессия НЕ выдаётся (ни HMAC-session, ни JWT-access)
        assert 'session' not in resp.cookies
        access_cookie = dj_settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
        assert access_cookie not in resp.cookies

    def test_post_invite_accept_invalid_token_400(self):
        """POST с несуществующим токеном → 400."""
        resp = APIClient().post(f'{BASE_AUTH}/invite/accept', {
            'token': 'nonexistent',
            'password': _PASSWORD,
        }, format='json')
        assert resp.status_code == 400
        assert 'error' in resp.data

    def test_post_invite_accept_short_password_400(self, invite_factory):
        """Пароль < 8 символов → 400 (валидация сериализатора)."""
        token, acc = invite_factory(email='__api_acc_sp__@example.com', role='manager')
        resp = APIClient().post(f'{BASE_AUTH}/invite/accept', {
            'token': token,
            'password': 'short',
        }, format='json')
        assert resp.status_code == 400

    def test_post_invite_accept_double_use_400(self, invite_factory):
        """Второй POST с тем же токеном → 400."""
        token, acc = invite_factory(email='__api_acc_dbl__@example.com', role='manager')
        r1 = APIClient().post(f'{BASE_AUTH}/invite/accept', {
            'token': token,
            'password': _PASSWORD,
        }, format='json')
        assert r1.status_code == 200
        r2 = APIClient().post(f'{BASE_AUTH}/invite/accept', {
            'token': token,
            'password': _PASSWORD,
        }, format='json')
        assert r2.status_code == 400


# ---------------------------------------------------------------------------
# e2e happy-path
# ---------------------------------------------------------------------------

def test_e2e_accept_enroll_login(invite_factory):
    """Полный happy-path: invite → accept → 2FA-enroll (TOTP) → JWT access cookie + recovery."""
    import pyotp
    from django.conf import settings as dj_settings

    token, acc = invite_factory(email='__e2e__@example.com', role='manager')
    c = APIClient()

    # 1. lookup
    assert c.get(f'{BASE_AUTH}/invite?token={token}').json()['valid'] is True

    # 2. accept → enroll-challenge (без сессии)
    r = c.post(f'{BASE_AUTH}/invite/accept',
               {'token': token, 'password': 'strongpass1'}, format='json')
    assert r.status_code == 200
    access_cookie = dj_settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    assert 'session' not in r.cookies
    assert access_cookie not in r.cookies
    challenge = r.json()['challenge_token']

    # 3. 2FA setup (TOTP)
    setup = c.post(f'{BASE_AUTH}/2fa/setup',
                   {'challenge_token': challenge, 'method': 'totp'}, format='json').json()
    code = pyotp.TOTP(setup['secret']).now()

    # 4. enable → JWT access cookie + recovery-коды
    en = c.post(f'{BASE_AUTH}/2fa/enable',
                {'challenge_token': challenge, 'code': code}, format='json')
    assert en.status_code == 200
    assert access_cookie in en.cookies
    body = en.json()
    assert body['redirect'] == '/admin'
    assert isinstance(body.get('recovery_codes'), list) and body['recovery_codes']
