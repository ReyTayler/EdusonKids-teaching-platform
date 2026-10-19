"""
test_auth_api.py — E2E тесты для /api/auth/* (DRF APIClient, реальная БД eduson_test).

Покрывает:
  - POST /login: teacher без 2FA → twofa_enrollment_required.
    неверный пароль, роль не совпала, locked-аккаунт, twofa_required (totp),
    twofa_enrollment_required (manager без 2FA).
  - POST /login/2fa: TOTP, recovery-код, неверный код.
  - email-OTP флоу.
  - POST /2fa/setup totp → secret+qr.
  - POST /2fa/enable с валидным TOTP → recovery_codes + access-cookie.
  - POST /2fa/disable с верным/неверным паролем.
  - GET /me → правильный shape.
  - POST /logout → clears cookie.

Фаза 4: аутентификация — JWT (access-cookie), не HMAC session-cookie.
account_factory использует Django hasher (password, is_active).
"""
from __future__ import annotations

from unittest.mock import patch

import pyotp
import pytest
from django.conf import settings
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

pytestmark = pytest.mark.django_db

_PASSWORD = 'secret123'

BASE = '/api/auth'

# Имя access-cookie из SIMPLE_JWT
_ACCESS_COOKIE = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jwt_client_for_account(acc: dict) -> APIClient:
    """
    Создать APIClient с JWT access-cookie для аккаунта из account_factory.

    Загружаем реальный Account по id и выдаём токен через RefreshToken.for_user.
    token_version берётся из реальной строки в БД.
    """
    from apps.accounts.models import Account
    user = Account.objects.get(pk=acc['id'])
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version
    client = APIClient()
    client.cookies[_ACCESS_COOKIE] = str(refresh.access_token)
    return client


# ---------------------------------------------------------------------------
# POST /login — teacher (без 2FA) → twofa_enrollment_required
# ---------------------------------------------------------------------------

def test_login_teacher_success(account_factory):
    """
    Teacher с паролем, без 2FA → twofa_enrollment_required (2FA обязательна для всех).
    """
    account_factory(
        email='__auth_login_ok__@example.com',
        role='teacher',
        password=_PASSWORD,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_login_ok__@example.com',
        'password': _PASSWORD,
        'role': 'teacher',
    }, format='json')
    assert resp.status_code == 200
    assert resp.data.get('twofa_enrollment_required') is True
    assert 'challenge_token' in resp.data
    # JWT-cookie не выдаётся до прохождения 2FA-enrollment
    assert _ACCESS_COOKIE not in resp.cookies


def test_login_teacher_with_2fa_gets_twofa_required(account_factory):
    """Teacher с включённым TOTP → twofa_required (не enrollment)."""
    secret = pyotp.random_base32()
    account_factory(
        email='__auth_teacher_totp__@example.com',
        role='teacher',
        password=_PASSWORD,
        twofa_method='totp',
        twofa_secret=secret,
        twofa_enabled=True,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_teacher_totp__@example.com',
        'password': _PASSWORD,
        'role': 'teacher',
    }, format='json')
    assert resp.status_code == 200
    assert resp.data.get('twofa_required') is True
    assert resp.data.get('method') == 'totp'
    assert 'challenge_token' in resp.data


def test_login_wrong_password(account_factory):
    account_factory(
        email='__auth_wp__@example.com',
        role='teacher',
        password=_PASSWORD,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_wp__@example.com',
        'password': 'wrongpass',
        'role': 'teacher',
    }, format='json')
    assert resp.status_code == 401
    assert 'error' in resp.data


def test_login_role_mismatch(account_factory):
    """Учитель пытается войти с кнопкой admin → 401."""
    account_factory(
        email='__auth_rm__@example.com',
        role='teacher',
        password=_PASSWORD,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_rm__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    assert resp.status_code == 401


def test_login_superadmin_via_admin_button(account_factory):
    """Суперадмин входит через кнопку «админ/менеджер» → НЕ 401 (role_matches учитывает superadmin)."""
    account_factory(
        email='__auth_super__@example.com',
        role='superadmin',
        password=_PASSWORD,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_super__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    assert resp.status_code != 401


def test_login_locked_account():
    """Заблокированный аккаунт → 429."""
    from django.contrib.auth.hashers import make_password
    pw_hash = make_password(_PASSWORD)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, is_superuser, "
            "first_name, last_name, failed_login_count, locked_until, date_joined) "
            "VALUES (%s, %s, 'manager', NULL, true, false, false, '', '', 5, now() + interval '10 minutes', NOW()) RETURNING id",
            ['__auth_locked__@example.com', pw_hash],
        )
        acc_id = cur.fetchone()[0]

    try:
        resp = APIClient().post(f'{BASE}/login', {
            'email': '__auth_locked__@example.com',
            'password': _PASSWORD,
            'role': 'admin',
        }, format='json')
        assert resp.status_code == 429
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM security_audit_log WHERE account_id = %s', [acc_id])
            cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])


def test_login_totp_enabled_returns_twofa_required(account_factory):
    """Аккаунт с включённым TOTP → twofa_required."""
    secret = pyotp.random_base32()
    account_factory(
        email='__auth_totp__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='totp',
        twofa_secret=secret,
        twofa_enabled=True,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_totp__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    assert resp.status_code == 200
    assert resp.data.get('twofa_required') is True
    assert resp.data.get('method') == 'totp'
    assert 'challenge_token' in resp.data


def test_login_manager_no_twofa_enrollment_required(account_factory):
    """Manager без 2FA → twofa_enrollment_required."""
    account_factory(
        email='__auth_enroll__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_enabled=False,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_enroll__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    assert resp.status_code == 200
    assert resp.data.get('twofa_enrollment_required') is True
    assert 'challenge_token' in resp.data


def test_login_email_twofa_sends_email(account_factory):
    """Аккаунт с email-методом 2FA — должен вызвать send_otp_email."""
    account_factory(
        email='__auth_email2fa__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='email',
        twofa_enabled=True,
    )
    with patch('apps.auth_app.services.send_otp_email') as mock_send:
        resp = APIClient().post(f'{BASE}/login', {
            'email': '__auth_email2fa__@example.com',
            'password': _PASSWORD,
            'role': 'admin',
        }, format='json')
    assert resp.status_code == 200
    assert resp.data.get('twofa_required') is True
    assert resp.data.get('method') == 'email'
    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# POST /login/2fa — TOTP
# ---------------------------------------------------------------------------

def test_login_2fa_totp_success(account_factory):
    secret = pyotp.random_base32()
    account_factory(
        email='__auth_2fa_ok__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='totp',
        twofa_secret=secret,
        twofa_enabled=True,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_2fa_ok__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    assert login_resp.status_code == 200
    challenge_token = login_resp.data['challenge_token']

    code = pyotp.TOTP(secret).now()
    resp = APIClient().post(f'{BASE}/login/2fa', {
        'challenge_token': challenge_token,
        'code': code,
    }, format='json')
    assert resp.status_code == 200
    assert resp.data['role'] == 'manager'
    # JWT access-cookie выдана после успешного 2FA
    assert _ACCESS_COOKIE in resp.cookies


def test_login_2fa_totp_wrong_code(account_factory):
    secret = pyotp.random_base32()
    account_factory(
        email='__auth_2fa_fail__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='totp',
        twofa_secret=secret,
        twofa_enabled=True,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_2fa_fail__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    challenge_token = login_resp.data['challenge_token']

    resp = APIClient().post(f'{BASE}/login/2fa', {
        'challenge_token': challenge_token,
        'code': '000000',
    }, format='json')
    assert resp.status_code == 401
    assert 'error' in resp.data


def test_login_2fa_recovery_code(account_factory):
    """Recovery-код проходит вместо TOTP."""
    from apps.auth_app import twofa as twofa_module

    secret_key = pyotp.random_base32()
    acc = account_factory(
        email='__auth_2fa_rec__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='totp',
        twofa_secret=secret_key,
        twofa_enabled=True,
    )
    rc = twofa_module.generate_recovery_codes(n=1)
    with connection.cursor() as cur:
        cur.execute('DELETE FROM account_recovery_codes WHERE account_id=%s', [acc['id']])
        cur.execute(
            'INSERT INTO account_recovery_codes (account_id, code_hash) VALUES (%s, %s)',
            [acc['id'], rc['hashes'][0]],
        )

    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_2fa_rec__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    challenge_token = login_resp.data['challenge_token']

    resp = APIClient().post(f'{BASE}/login/2fa', {
        'challenge_token': challenge_token,
        'code': rc['plain'][0],
    }, format='json')
    assert resp.status_code == 200
    assert _ACCESS_COOKIE in resp.cookies


# ---------------------------------------------------------------------------
# POST /2fa/email/send
# ---------------------------------------------------------------------------

def test_email_send_requires_valid_challenge():
    """Просроченный/невалидный challenge_token → 401."""
    resp = APIClient().post(f'{BASE}/2fa/email/send', {
        'challenge_token': 'invalid.token',
    }, format='json')
    assert resp.status_code == 401


def test_email_send_success(account_factory):
    """
    /2fa/email/send принимает login_challenge (stage=enroll или email-2FA).
    Тестируем enrollment resend: manager без 2FA → enroll challenge →
    setup email → resend с enroll challenge_token.
    """
    account_factory(
        email='__auth_esend__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='email',
        twofa_enabled=False,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_esend__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    assert login_resp.data.get('twofa_enrollment_required') is True
    enroll_challenge = login_resp.data['challenge_token']

    with patch('apps.auth_app.services.send_otp_email'):
        APIClient().post(f'{BASE}/2fa/setup', {
            'challenge_token': enroll_challenge,
            'method': 'email',
        }, format='json')

    with patch('apps.auth_app.services.send_otp_email') as mock_send:
        resp = APIClient().post(f'{BASE}/2fa/email/send', {
            'challenge_token': enroll_challenge,
        }, format='json')
    assert resp.status_code == 200
    assert 'challenge_token' in resp.data
    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# POST /2fa/setup + POST /2fa/enable (enrollment flow)
# ---------------------------------------------------------------------------

def test_twofa_setup_totp(account_factory):
    """Setup totp → получаем secret + qr."""
    account_factory(
        email='__auth_setup__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_enabled=False,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_setup__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    assert login_resp.data.get('twofa_enrollment_required') is True
    challenge_token = login_resp.data['challenge_token']

    resp = APIClient().post(f'{BASE}/2fa/setup', {
        'challenge_token': challenge_token,
        'method': 'totp',
    }, format='json')
    assert resp.status_code == 200
    assert resp.data['method'] == 'totp'
    assert 'secret' in resp.data
    assert resp.data.get('qr', '').startswith('data:image/png;base64,')


def test_twofa_enable_totp(account_factory):
    """Полный enrollment: setup → enable с валидным TOTP → recovery_codes + access-cookie."""
    account_factory(
        email='__auth_enable__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_enabled=False,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_enable__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    challenge_token = login_resp.data['challenge_token']

    setup_resp = APIClient().post(f'{BASE}/2fa/setup', {
        'challenge_token': challenge_token,
        'method': 'totp',
    }, format='json')
    totp_secret = setup_resp.data['secret']

    code = pyotp.TOTP(totp_secret).now()
    enable_resp = APIClient().post(f'{BASE}/2fa/enable', {
        'challenge_token': challenge_token,
        'code': code,
    }, format='json')
    assert enable_resp.status_code == 200
    assert len(enable_resp.data.get('recovery_codes', [])) == 8
    # JWT access-cookie выдана
    assert _ACCESS_COOKIE in enable_resp.cookies


def test_twofa_enable_wrong_code(account_factory):
    """Неверный код при enable → 401."""
    account_factory(
        email='__auth_en_fail__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_enabled=False,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_en_fail__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    challenge_token = login_resp.data['challenge_token']

    APIClient().post(f'{BASE}/2fa/setup', {
        'challenge_token': challenge_token,
        'method': 'totp',
    }, format='json')

    enable_resp = APIClient().post(f'{BASE}/2fa/enable', {
        'challenge_token': challenge_token,
        'code': '000000',
    }, format='json')
    assert enable_resp.status_code == 401


def test_twofa_enable_email(account_factory):
    """Email-enrollment: setup email → код из письма → enable → 200 + recovery + access-cookie."""
    account_factory(
        email='__auth_en_email__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_enabled=False,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_en_email__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    enroll_challenge = login_resp.data['challenge_token']

    with patch('apps.auth_app.services.send_otp_email') as mock_send:
        setup_resp = APIClient().post(f'{BASE}/2fa/setup', {
            'challenge_token': enroll_challenge,
            'method': 'email',
        }, format='json')
    assert setup_resp.status_code == 200
    assert setup_resp.data['method'] == 'email'
    email_challenge = setup_resp.data['challenge_token']
    code = mock_send.call_args[0][1]  # send_otp_email(email, code)

    enable_resp = APIClient().post(f'{BASE}/2fa/enable', {
        'challenge_token': email_challenge,
        'code': code,
    }, format='json')
    assert enable_resp.status_code == 200
    assert len(enable_resp.data.get('recovery_codes', [])) == 8
    assert _ACCESS_COOKIE in enable_resp.cookies


def test_twofa_enable_access_cookie_authenticates(account_factory):
    """
    После enable выданный access-cookie должен СРАЗУ аутентифицировать.

    Регрессия: enable дёргает bump_token_version, но cookie выписывалась со старым
    token_version → аутентификация её отвергала (401). Cookie обязана нести НОВУЮ версию.
    """
    account_factory(
        email='__auth_en_sess__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_enabled=False,
    )
    login_resp = APIClient().post(f'{BASE}/login', {
        'email': '__auth_en_sess__@example.com',
        'password': _PASSWORD,
        'role': 'admin',
    }, format='json')
    ch = login_resp.data['challenge_token']
    setup_resp = APIClient().post(f'{BASE}/2fa/setup', {
        'challenge_token': ch, 'method': 'totp',
    }, format='json')
    code = pyotp.TOTP(setup_resp.data['secret']).now()
    enable_resp = APIClient().post(f'{BASE}/2fa/enable', {
        'challenge_token': ch, 'code': code,
    }, format='json')
    assert enable_resp.status_code == 200

    # Используем выданный access-cookie для аутентифицированного запроса.
    access_value = enable_resp.cookies[_ACCESS_COOKIE].value
    client = APIClient()
    client.cookies[_ACCESS_COOKIE] = access_value
    me_resp = client.get(f'{BASE}/me')
    assert me_resp.status_code == 200, 'access-cookie после enable должна аутентифицировать'


# ---------------------------------------------------------------------------
# POST /2fa/disable
# ---------------------------------------------------------------------------

def test_twofa_disable_correct_password(account_factory):
    """Верный пароль → 2FA сброшена."""
    secret_key = pyotp.random_base32()
    acc = account_factory(
        email='__auth_dis__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='totp',
        twofa_secret=secret_key,
        twofa_enabled=True,
    )
    c = _jwt_client_for_account(acc)
    resp = c.post(f'{BASE}/2fa/disable', {'password': _PASSWORD}, format='json')
    assert resp.status_code == 200
    assert resp.data == {'ok': True}


def test_twofa_disable_wrong_password(account_factory):
    """Неверный пароль → 401."""
    acc = account_factory(
        email='__auth_dis_fail__@example.com',
        role='manager',
        password=_PASSWORD,
        twofa_method='totp',
        twofa_secret=pyotp.random_base32(),
        twofa_enabled=True,
    )
    c = _jwt_client_for_account(acc)
    resp = c.post(f'{BASE}/2fa/disable', {'password': 'wrongpass'}, format='json')
    assert resp.status_code == 401


def test_twofa_disable_no_auth():
    """Без JWT-cookie → 401."""
    resp = APIClient().post(f'{BASE}/2fa/disable', {'password': 'x'}, format='json')
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------

def test_me_returns_correct_shape(account_factory):
    acc = account_factory(
        email='__auth_me__@example.com',
        role='manager',
        password=_PASSWORD,
    )
    c = _jwt_client_for_account(acc)
    resp = c.get(f'{BASE}/me')
    assert resp.status_code == 200
    data = resp.data
    assert data['account_id'] == acc['id']
    assert data['email'] == '__auth_me__@example.com'
    assert data['role'] == 'manager'
    assert 'teacher_id' in data
    assert 'name' in data
    assert 'twofa_enabled' in data


def test_me_name_prefers_full_name_over_email(account_factory):
    """full_name (manager/admin) должен перекрывать email-fallback в /me."""
    from apps.accounts import repository as accounts_repo

    acc = account_factory(
        email='__auth_me_fullname__@example.com',
        role='manager',
        password=_PASSWORD,
    )
    accounts_repo.update_full_name(acc['id'], 'Иван Тестов')
    c = _jwt_client_for_account(acc)
    resp = c.get(f'{BASE}/me')
    assert resp.status_code == 200
    assert resp.data['name'] == 'Иван Тестов'


def test_me_name_uses_teacher_name_for_teacher_account(account_factory):
    """Для teacher-учётки /me должен отдавать имя преподавателя, а не email."""
    acc = account_factory(role='teacher', password=_PASSWORD)
    c = _jwt_client_for_account(acc)
    resp = c.get(f'{BASE}/me')
    assert resp.status_code == 200
    assert resp.data['name'] == '__auth_teacher__'


def test_me_unauthenticated():
    resp = APIClient().get(f'{BASE}/me')
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------

def test_logout_clears_cookie(account_factory):
    acc = account_factory(
        email='__auth_logout__@example.com',
        role='teacher',
        password=_PASSWORD,
    )
    c = _jwt_client_for_account(acc)
    resp = c.post(f'{BASE}/logout', format='json')
    assert resp.status_code == 200
    assert resp.data == {'ok': True}
    # access-cookie должна быть очищена (max_age=0 или пустое значение)
    access_cookie_morsel = resp.cookies.get(_ACCESS_COOKIE)
    if access_cookie_morsel:
        assert access_cookie_morsel.value == '' or access_cookie_morsel['max-age'] == 0


def test_logout_no_auth():
    """Без JWT-cookie → LogoutView требует IsAuthenticated → 401."""
    resp = APIClient().post(f'{BASE}/logout', format='json')
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Task 5.1 — 2FA обязательна для ВСЕХ ролей (включая teacher)
# ---------------------------------------------------------------------------

def test_legacy_teacher_without_2fa_enrolls(account_factory):
    """
    Task 5.1: teacher с паролем, без 2FA (legacy-данные) → twofa_enrollment_required.
    """
    account_factory(
        email='__lt2__@example.com',
        role='teacher',
        password=_PASSWORD,
        twofa_enabled=False,
    )
    resp = APIClient().post(f'{BASE}/login', {
        'email': '__lt2__@example.com',
        'password': _PASSWORD,
        'role': 'teacher',
    }, format='json')
    assert resp.status_code == 200
    assert resp.data.get('twofa_enrollment_required') is True
    assert 'challenge_token' in resp.data
    assert _ACCESS_COOKIE not in resp.cookies
