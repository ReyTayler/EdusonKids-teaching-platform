"""
apps/core/utils/passwords.py — утилиты для invite-токенов.

generate_invite_token и hash_invite_token вынесены сюда как shared-утилиты,
используемые в apps.accounts.services, apps.auth_app.services и тестах.
"""
from __future__ import annotations

import hashlib
import secrets


def generate_invite_token() -> tuple[str, str]:
    """Генерирует (plaintext, sha256hex) для invite-токена."""
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode('utf-8')).hexdigest()
    return plaintext, token_hash


def hash_invite_token(token: str) -> str:
    """SHA-256 hex от plaintext invite-токена. Для поиска по hash в БД."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()
