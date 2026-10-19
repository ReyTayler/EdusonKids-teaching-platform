"""
twofa.py — порт services/twofa.js.

Зависимости: pyotp (TOTP), qrcode (QR PNG), bcrypt (обратная совместимость legacy recovery-хешей).
Отличия от otplib:
  - pyotp.TOTP.verify(valid_window=1) ≈ otplib epochTolerance=30s (±1 шаг 30 с).
  - generate_secret() через pyotp.random_base32() → base32 строка (otplib.generateSecret тоже base32).
  - generate_recovery_codes: secrets.token_hex(5) — точный порт crypto.randomBytes(5).toString('hex').

Фаза 2 (architecture_v2.md):
  - _verify_with_secret (ручной HMAC-порт auth.js) УДАЛЁН — заменён на django.core.signing.
  - issue_email_challenge / verify_email_challenge переведены на TimestampSigner.sign_object /
    unsign_object(max_age=...) — тот же паттерн, что в auth_app/services.py.
  - email-OTP code_hash: make_password / check_password вместо bcrypt (эфемерный токен, 5 мин).
  - recovery-коды: НОВЫЕ хеши — make_password; СТАРЫЕ bcrypt-хеши ($2a$/$2b$/$2y$) — legacy-ветка
    через bcrypt.checkpw для обратной совместимости. Убрать legacy после миграции всех recovery-кодов.
"""
from __future__ import annotations

import base64
import io
import secrets
from typing import Optional

import bcrypt
import pyotp
import qrcode
from django.contrib.auth.hashers import check_password, make_password
from django.core.signing import BadSignature, TimestampSigner

# Имя школы в приложении-аутентификаторе (подпись записи TOTP).
ISSUER = 'Eduson Kids'

# TTL email-challenge: 5 минут в секундах (тот же TTL, что и login-challenge).
EMAIL_CHALLENGE_TTL = 5 * 60

# Подписант для email-challenge-токенов (тот же паттерн, что _signer в services.py).
_email_signer = TimestampSigner()


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------

def generate_secret() -> str:
    """Base32 секрет для TOTP. Порт generateSecret (otplib.generateSecret)."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    """otpauth:// URI для QR-кода. Порт provisioningUri."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_data_url(uri: str) -> str:
    """PNG QR-код в data:image/png;base64,... Порт qrDataUrl."""
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/png;base64,{b64}'


def verify_totp(secret: Optional[str], code) -> bool:
    """
    Проверить TOTP-код. Порт verifyTotp.

    valid_window=1 ≈ otplib epochTolerance:30s — принимает предыдущий/текущий/следующий шаг.
    """
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(str(code), valid_window=1)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# email-OTP (stateless challenge)
# ---------------------------------------------------------------------------

def generate_email_code() -> str:
    """6-значный код. Порт generateEmailCode (crypto.randomInt(0,1e6).padStart(6,'0'))."""
    return str(secrets.randbelow(1_000_000)).zfill(6)


def issue_email_challenge(account_id: int) -> dict:
    """
    Создать stateless email-challenge. Порт issueEmailChallenge.

    code_hash: make_password (Django hasher) — код эфемерный (5 мин), миграции хешей нет.
    challenge: TimestampSigner.sign_object с kind='email2fa'.
    TTL встроен в TimestampSigner — не кладём собственный exp в payload.
    Возвращает {code, challenge}.
    """
    code = generate_email_code()
    code_hash = make_password(code)
    payload = {
        'kind': 'email2fa',
        'account_id': account_id,
        'code_hash': code_hash,
    }
    challenge = _email_signer.sign_object(payload)
    return {'code': code, 'challenge': challenge}


def verify_email_challenge(challenge: str, code) -> dict:
    """
    Верифицировать email-challenge. Порт verifyEmailChallenge.

    Использует TimestampSigner.unsign_object(max_age=EMAIL_CHALLENGE_TTL) — встроенный TTL.
    Возвращает {'ok': True, 'account_id': int} или {'ok': False}.
    """
    if not challenge:
        return {'ok': False}
    try:
        payload = _email_signer.unsign_object(challenge, max_age=EMAIL_CHALLENGE_TTL)
    except BadSignature:
        return {'ok': False}
    if not payload or payload.get('kind') != 'email2fa':
        return {'ok': False}
    try:
        ok = check_password(str(code), payload['code_hash'])
    except Exception:  # noqa: BLE001
        return {'ok': False}
    if ok:
        return {'ok': True, 'account_id': payload['account_id']}
    return {'ok': False}


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------

def generate_recovery_codes(n: int = 8) -> dict:
    """
    Генерировать recovery-коды. Порт generateRecoveryCodes.

    plain: [secrets.token_hex(5) ...] — точный порт crypto.randomBytes(5).toString('hex')
    hashes: make_password каждого (Django hasher).
    """
    plain = [secrets.token_hex(5) for _ in range(n)]
    hashes = [make_password(c) for c in plain]
    return {'plain': plain, 'hashes': hashes}


def verify_recovery(code, code_hash: Optional[str]) -> bool:
    """
    Проверить recovery-код. Порт verifyRecovery.

    Обратная совместимость: recovery-коды, созданные до Фазы 2, хранят bcrypt-хеши
    вида $2a$/$2b$/$2y$ (bcrypt.hashpw cost=8). Django check_password их не распознаёт.
    Поэтому: если prefix — bcrypt, проверяем через bcrypt.checkpw (legacy-ветка).
    Иначе — check_password (новые хеши Django hasher).
    Убрать legacy-ветку после миграции всех recovery-кодов на Django hasher.
    """
    if not code or not code_hash:
        return False
    # Legacy: bcrypt-хеши, сгенерированные до Фазы 2 (прямой bcrypt.hashpw cost=8).
    if code_hash.startswith(('$2a$', '$2b$', '$2y$')):
        try:
            return bcrypt.checkpw(str(code).encode('utf-8'), code_hash.encode('utf-8'))
        except Exception:  # noqa: BLE001
            return False
    # Новые хеши Django hasher (make_password).
    try:
        return check_password(str(code), code_hash)
    except Exception:  # noqa: BLE001
        return False
