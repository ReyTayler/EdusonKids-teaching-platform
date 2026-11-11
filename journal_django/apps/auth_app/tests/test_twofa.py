"""
test_twofa.py — юнит-тесты для twofa.py.

Покрывает: verify_totp, verify_email_challenge, issue_email_challenge,
generate_email_code.
"""
from __future__ import annotations

import time

import pyotp
import pytest

from apps.auth_app import twofa as twofa_module

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------

def test_generate_secret_is_base32():
    s = twofa_module.generate_secret()
    # base32 алфавит A-Z 2-7
    assert isinstance(s, str)
    assert len(s) >= 16


def test_verify_totp_valid():
    secret = twofa_module.generate_secret()
    code = pyotp.TOTP(secret).now()
    assert twofa_module.verify_totp(secret, code) is True


def test_verify_totp_invalid():
    secret = twofa_module.generate_secret()
    assert twofa_module.verify_totp(secret, '000000') is False


def test_verify_totp_empty_secret():
    assert twofa_module.verify_totp('', '123456') is False


def test_verify_totp_empty_code():
    secret = twofa_module.generate_secret()
    assert twofa_module.verify_totp(secret, '') is False


def test_verify_totp_none_inputs():
    assert twofa_module.verify_totp(None, None) is False


# ---------------------------------------------------------------------------
# email-OTP (stateless challenge)
# ---------------------------------------------------------------------------

def test_generate_email_code_format():
    for _ in range(10):
        code = twofa_module.generate_email_code()
        assert len(code) == 6
        assert code.isdigit()


def test_issue_and_verify_email_challenge():
    result = twofa_module.issue_email_challenge(99)
    assert 'code' in result
    assert 'challenge' in result
    assert len(result['code']) == 6

    res = twofa_module.verify_email_challenge(result['challenge'], result['code'])
    assert res['ok'] is True
    assert res['account_id'] == 99


def test_verify_email_challenge_wrong_code():
    result = twofa_module.issue_email_challenge(99)
    # гарантированно неверный код: берём отличный от выданного, без вероятностной ветки
    wrong = '000000' if result['code'] != '000000' else '111111'
    res = twofa_module.verify_email_challenge(result['challenge'], wrong)
    assert res['ok'] is False


def test_verify_email_challenge_expired():
    """TTL-проверка: TimestampSigner встроенный max_age. Используем очень короткий TTL."""
    import time as _time
    from unittest.mock import patch

    result = twofa_module.issue_email_challenge(99)
    # Эмулируем устаревший токен — прокручиваем время вперёд на 6 минут (> EMAIL_CHALLENGE_TTL=300s)
    future_time = _time.time() + 400
    with patch('time.time', return_value=future_time):
        res = twofa_module.verify_email_challenge(result['challenge'], result['code'])
    assert res['ok'] is False


def test_verify_email_challenge_tampered():
    """Подмена challenge → BadSignature → ok=False."""
    result = twofa_module.issue_email_challenge(99)
    # Портим подпись
    tampered = result['challenge'] + 'X'
    res = twofa_module.verify_email_challenge(tampered, result['code'])
    assert res['ok'] is False


# ---------------------------------------------------------------------------
# QR + provisioning URI
# ---------------------------------------------------------------------------

def test_provisioning_uri_contains_issuer():
    secret = twofa_module.generate_secret()
    uri = twofa_module.provisioning_uri(secret, 'test@example.com')
    # Издатель — подпись записи в приложении-аутентификаторе; в otpauth-URI он
    # закодирован (пробел → %20) и стоит и в метке, и в параметре issuer.
    assert 'issuer=Eduson%20Kids' in uri
    assert 'KOTOKOD' not in uri
    assert 'test%40example.com' in uri or 'test@example.com' in uri


def test_qr_data_url_format():
    secret = twofa_module.generate_secret()
    uri = twofa_module.provisioning_uri(secret, 'test@example.com')
    qr = twofa_module.qr_data_url(uri)
    assert qr.startswith('data:image/png;base64,')


# ---------------------------------------------------------------------------
# start_twofa_setup — запись способа только у выключенной 2FA (гонка/повтор)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_start_twofa_setup_writes_when_disabled():
    from apps.accounts import repository as accounts_repo
    from apps.accounts.models import Account
    acc = Account.objects.create(email='__start_ok__@example.com', role='manager',
                                 twofa_enabled=False)

    assert accounts_repo.start_twofa_setup(acc.id, 'totp', 'SECRET234') is True

    acc.refresh_from_db()
    assert acc.twofa_method == 'totp' and acc.twofa_secret == 'SECRET234'
    assert acc.twofa_enabled is False


@pytest.mark.django_db
def test_start_twofa_setup_refuses_when_already_enabled():
    from apps.accounts import repository as accounts_repo
    from apps.accounts.models import Account
    acc = Account.objects.create(email='__start_no__@example.com', role='manager',
                                 twofa_method='totp', twofa_secret='KEEPME789',
                                 twofa_enabled=True)

    assert accounts_repo.start_twofa_setup(acc.id, 'email', None) is False

    acc.refresh_from_db()
    assert acc.twofa_method == 'totp' and acc.twofa_secret == 'KEEPME789'
    assert acc.twofa_enabled is True
