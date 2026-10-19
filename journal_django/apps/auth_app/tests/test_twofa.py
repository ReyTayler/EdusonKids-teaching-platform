"""
test_twofa.py — юнит-тесты для twofa.py.

Покрывает: verify_totp, generate_recovery_codes, verify_email_challenge,
issue_email_challenge, verify_recovery, generate_email_code.
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
# Recovery codes
# ---------------------------------------------------------------------------

def test_generate_recovery_codes_count():
    rc = twofa_module.generate_recovery_codes(n=8)
    assert len(rc['plain']) == 8
    assert len(rc['hashes']) == 8


def test_generate_recovery_codes_format():
    rc = twofa_module.generate_recovery_codes(n=3)
    for code in rc['plain']:
        # secrets.token_hex(5) → 10 hex символов
        assert len(code) == 10
        assert all(c in '0123456789abcdef' for c in code)


def test_verify_recovery_valid():
    rc = twofa_module.generate_recovery_codes(n=1)
    assert twofa_module.verify_recovery(rc['plain'][0], rc['hashes'][0]) is True


def test_verify_recovery_invalid():
    rc = twofa_module.generate_recovery_codes(n=1)
    assert twofa_module.verify_recovery('wrongcode', rc['hashes'][0]) is False


def test_verify_recovery_empty():
    assert twofa_module.verify_recovery('', None) is False
    assert twofa_module.verify_recovery(None, 'hash') is False


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
