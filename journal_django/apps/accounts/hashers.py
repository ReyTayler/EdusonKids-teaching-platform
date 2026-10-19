"""
apps/accounts/hashers.py — хэшер для паролей, перенесённых из Eduson (bcryptjs).

ПОЧЕМУ ОБЁРТКА, а не голый BCryptPasswordHasher: штатный способ Django
«Password upgrading without requiring a login» (пример PBKDF2WrappedSHA1PasswordHasher).
Голый bcrypt cost 10 проверяется в разы быстрее PBKDF2 — по времени ответа /login
было бы видно, какие перенесённые учётки существуют, а в БД до первого входа
лежали бы слабые хэши. Обёртка = PBKDF2 (итерации по умолчанию) поверх готовой
строки bcrypt: импорт оборачивает хэши сразу, пароль для этого не нужен.

ФОРМАТ: pbkdf2_wrapped_bcrypt$<iterations>$<salt>$<pbkdf2-hash>, где
salt = <версия 2a|2b|2y><cost 2 цифры><22 символа bcrypt-соли>, например
'2a10N9qo8uLOickgx2ZMRZoMye'. Соль bcrypt нужно хранить, иначе при проверке не
вычислить тот же bcrypt-хэш. В поле salt нельзя '$' (его режет decode и запрещает
_check_encode_args), поэтому '$2a$10$' сжат в фиксированные 4 символа — разбор
по позициям, без разделителя (алфавит bcrypt содержит '.', '/' — разделитель
пришлось бы экранировать). Та же строка служит и солью PBKDF2, как в примере
документации с sha1-солью: ~128 бит случайности bcrypt-соли достаточно.

После успешного входа Django сам перехэширует пароль в основной алгоритм
(первый в PASSWORD_HASHERS), и обёрнутых хэшей становится всё меньше.

Время проверки обёртки больше обычного PBKDF2 на один bcrypt(cost 10) ≈ 50 мс
(~17%), как и в примере документации; при rate-limit на /login это приемлемо.
"""
from __future__ import annotations

import re

import bcrypt
from django.contrib.auth.hashers import PBKDF2PasswordHasher

# bcrypt modular crypt: $2a$10$ + 22 символа соли + 31 символ хэша = 60 символов.
_BCRYPT_RE = re.compile(r'^\$(2[aby])\$(\d{2})\$([./A-Za-z0-9]{22})[./A-Za-z0-9]{31}$')
_SALT_RE = re.compile(r'^(2[aby])(\d{2})([./A-Za-z0-9]{22})$')
# Потолок 14, не 31: хэш с cost 25–31 вешал бы воркер на каждую попытку входа
# (у Eduson cost 10).
_MIN_COST, _MAX_COST = 4, 14


def _salt_from_bcrypt(bcrypt_hash: str) -> str:
    m = _BCRYPT_RE.fullmatch(bcrypt_hash or '')
    if not m or not (_MIN_COST <= int(m.group(2)) <= _MAX_COST):
        raise ValueError('Ожидается bcrypt-хэш вида $2a$/$2b$/$2y$<cost 04..14>$<53 символа>')
    version, cost, salt22 = m.groups()
    return f'{version}{cost}{salt22}'


def _bcrypt_salt(salt: str) -> bytes:
    m = _SALT_RE.fullmatch(salt or '')
    if not m:
        raise ValueError('Соль не в формате pbkdf2_wrapped_bcrypt')
    version, cost, salt22 = m.groups()
    return f'${version}${cost}${salt22}'.encode('ascii')


class PBKDF2WrappedBCryptPasswordHasher(PBKDF2PasswordHasher):
    algorithm = 'pbkdf2_wrapped_bcrypt'

    def salt(self):
        # Нужен для make_password(..., hasher=...); в проде новые хэши этим
        # алгоритмом не создаются (основной — PBKDF2), но формат обязан быть полным.
        return _salt_from_bcrypt(
            bcrypt.hashpw(b'', bcrypt.gensalt(10, prefix=b'2a')).decode('ascii')
        )

    def encode_bcrypt_hash(self, bcrypt_hash, salt, iterations=None):
        # Соль обязана совпадать с солью внутри bcrypt-хэша: иначе encode() при
        # проверке посчитает другой bcrypt и пароль не подойдёт никогда.
        if _salt_from_bcrypt(bcrypt_hash) != salt:
            raise ValueError('Соль не соответствует bcrypt-хэшу')
        return super().encode(bcrypt_hash, salt, iterations)

    def encode(self, password, salt, iterations=None):
        # bcrypt 4.x молча усекает пароль до 72 байт — так же, как bcryptjs в Eduson.
        bcrypt_hash = bcrypt.hashpw(password.encode('utf-8'), _bcrypt_salt(salt)).decode('ascii')
        return self.encode_bcrypt_hash(bcrypt_hash, salt, iterations)


def wrap_eduson_bcrypt(bcrypt_hash: str) -> str:
    """Строка для Account.password из bcryptjs-хэша Eduson ($2a$10$…). Неверный формат → ValueError."""
    salt = _salt_from_bcrypt(bcrypt_hash)
    return PBKDF2WrappedBCryptPasswordHasher().encode_bcrypt_hash(bcrypt_hash, salt)
