"""
test_eduson_bcrypt.py — вход старыми паролями Eduson (bcryptjs).

Импорт оборачивает хэши bcryptjs (`$2a$10$...`) в PBKDF2 через
apps.accounts.hashers.wrap_eduson_bcrypt. Проверяем, что реальный путь входа
(authenticate → ModelBackend → Account.check_password) их принимает и после
успеха перехэширует в основной алгоритм (pbkdf2_sha256).
"""
from __future__ import annotations

import time

import bcrypt
import pytest
from django.conf import settings
from django.contrib.auth.hashers import (
    PBKDF2PasswordHasher, check_password, identify_hasher, make_password,
)
from django.db import connection
from rest_framework.test import APIClient

from apps.accounts.hashers import PBKDF2WrappedBCryptPasswordHasher, wrap_eduson_bcrypt

BASE = '/api/auth'
_RAW = 'секрет-123'
_ACCESS_COOKIE = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')


def _eduson_hash(raw: str) -> str:
    # так хранит bcryptjs: $2a$10$...
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt(10, prefix=b'2a')).decode()


def _set_stored_password(account_id: int, stored: str) -> None:
    with connection.cursor() as cur:
        cur.execute('UPDATE accounts SET password = %s WHERE id = %s', [stored, account_id])


def _stored_password(account_id: int) -> str:
    with connection.cursor() as cur:
        cur.execute('SELECT password FROM accounts WHERE id = %s', [account_id])
        return cur.fetchone()[0]


def _login(email: str, password: str):
    return APIClient().post(f'{BASE}/login', {
        'email': email, 'password': password, 'role': 'teacher',
    }, format='json')


# ---------------------------------------------------------------------------
# Хэшер и wrap_eduson_bcrypt (без БД)
# ---------------------------------------------------------------------------

def test_wrapped_hash_verifies_and_offers_upgrade():
    stored = wrap_eduson_bcrypt(_eduson_hash(_RAW))
    assert stored.startswith('pbkdf2_wrapped_bcrypt$')
    assert '$2a$' not in stored          # исходный bcrypt-хэш в открытом виде не хранится
    upgraded = {}
    assert check_password(_RAW, stored, setter=lambda raw: upgraded.setdefault('raw', raw))
    assert upgraded['raw'] == _RAW       # Django предложил перехэшировать
    assert not check_password('не тот', stored)
    assert identify_hasher(make_password(_RAW)).algorithm == 'pbkdf2_sha256'


@pytest.mark.parametrize('prefix', [b'2a', b'2b'])
def test_wrap_accepts_bcrypt_versions(prefix):
    h = bcrypt.hashpw(_RAW.encode(), bcrypt.gensalt(4, prefix=prefix)).decode()
    assert check_password(_RAW, wrap_eduson_bcrypt(h))


def test_wrap_accepts_2y():
    h = bcrypt.hashpw(_RAW.encode(), bcrypt.gensalt(4)).decode().replace('$2b$', '$2y$', 1)
    assert check_password(_RAW, wrap_eduson_bcrypt(h))


@pytest.mark.parametrize('bad', [
    '',
    'not-a-hash',
    'bcrypt$$2a$10$' + 'a' * 53,              # уже с префиксом Django
    '$2x$10$' + 'a' * 53,                    # неподдерживаемая версия
    '$2a$10$' + 'a' * 52,                    # короче на символ
    '$2a$10$' + 'a' * 54,                    # длиннее на символ
    '$2a$10$' + 'a' * 52 + '!',              # символ вне алфавита bcrypt
    '$2a$03$' + 'a' * 53,                    # cost вне 4..31
    '$2a$15$' + 'a' * 53,                    # выше потолка 14 (вешал бы воркер)
    '$2a$32$' + 'a' * 53,
    'pbkdf2_sha256$1000000$salt$hash',
])
def test_wrap_rejects_invalid_input(bad):
    with pytest.raises(ValueError):
        wrap_eduson_bcrypt(bad)


def test_wrapped_hash_uses_default_pbkdf2_iterations():
    # Страж против тайминг-утечки: обёртка обязана стоить как обычный PBKDF2.
    stored = wrap_eduson_bcrypt(_eduson_hash(_RAW))
    decoded = PBKDF2WrappedBCryptPasswordHasher().decode(stored)
    assert decoded['iterations'] == PBKDF2PasswordHasher.iterations


def test_wrapped_check_not_faster_than_plain_pbkdf2():
    wrapped = wrap_eduson_bcrypt(_eduson_hash(_RAW))
    plain = make_password(_RAW)

    def best_of(encoded):
        # Минимум из нескольких замеров гасит шум планировщика ОС.
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            check_password('не тот', encoded)
            times.append(time.perf_counter() - t0)
        return min(times)

    assert best_of(wrapped) >= 0.5 * best_of(plain)


# ---------------------------------------------------------------------------
# Реальный эндпоинт входа
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_login_with_wrapped_password_succeeds_and_rehashes(account_factory):
    acc = account_factory(email='__eduson_bcrypt_ok__@example.com', role='teacher')
    _set_stored_password(acc['id'], wrap_eduson_bcrypt(_eduson_hash(_RAW)))

    resp = _login('__eduson_bcrypt_ok__@example.com', _RAW)

    # Тот же ответ, что у обычной учётки без 2FA: обязательная настройка 2FA.
    assert resp.status_code == 200, resp.data
    assert resp.data.get('twofa_enrollment_required') is True
    assert 'challenge_token' in resp.data
    assert _ACCESS_COOKIE not in resp.cookies

    stored = _stored_password(acc['id'])
    assert identify_hasher(stored).algorithm == 'pbkdf2_sha256'
    assert check_password(_RAW, stored)


@pytest.mark.django_db
def test_login_with_wrapped_password_wrong_password_rejected(account_factory):
    acc = account_factory(email='__eduson_bcrypt_wp__@example.com', role='teacher')
    original = wrap_eduson_bcrypt(_eduson_hash(_RAW))
    _set_stored_password(acc['id'], original)

    resp = _login('__eduson_bcrypt_wp__@example.com', 'не тот')

    assert resp.status_code == 401
    assert 'error' in resp.data
    assert _stored_password(acc['id']) == original   # неудачный вход хэш не трогает


@pytest.mark.django_db
def test_login_with_password_longer_than_72_bytes_does_not_crash(account_factory):
    # Страж обновления bcrypt: 4.x усекает >72 байт (как bcryptjs), 5.x бросает
    # ValueError → вход упал бы 500. больше 72 байт кириллицей.
    long_raw = 'пароль' * 7  # 42 символа, 84 байта
    assert len(long_raw.encode()) > 72
    acc = account_factory(email='__eduson_bcrypt_long__@example.com', role='teacher')
    _set_stored_password(acc['id'], wrap_eduson_bcrypt(_eduson_hash(long_raw)))
    assert _login('__eduson_bcrypt_long__@example.com', long_raw).status_code == 200
    assert check_password(long_raw, _stored_password(acc['id']))  # перехэш — полной строки

    # Длинный неверный пароль к короткому хэшу — 401, не 500.
    _set_stored_password(acc['id'], wrap_eduson_bcrypt(_eduson_hash(_RAW)))
    assert _login('__eduson_bcrypt_long__@example.com', long_raw).status_code == 401


@pytest.mark.django_db
def test_login_inactive_account_with_wrapped_password_gets_no_tokens(account_factory):
    acc = account_factory(
        email='__eduson_bcrypt_off__@example.com', role='teacher', is_active=False,
    )
    _set_stored_password(acc['id'], wrap_eduson_bcrypt(_eduson_hash(_RAW)))

    resp = _login('__eduson_bcrypt_off__@example.com', _RAW)

    assert resp.status_code == 401
    assert 'challenge_token' not in resp.data
    assert _ACCESS_COOKIE not in resp.cookies
