"""
AccountsService — бизнес-логика accounts (порт routes/admin/accounts.js).

Здесь: создание учёток через invite-ссылку, проверка дубля email, вырезание
секретов из выдачи, аудит мутаций. SQL — только через repository.

⚠️ password_hash и twofa_secret НИКОГДА не покидают этот слой.
"""
from __future__ import annotations

import datetime
from typing import Optional

from django.db import transaction
from django.utils import timezone
from rest_framework.request import Request

from apps.accounts import repository
from apps.audit.services import log_event


import hashlib
import secrets

INVITE_TTL_HOURS = 48
TOKEN_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

def generate_invite_token():
    """Генерирует (plaintext, sha256hex) для invite-токена."""
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode('utf-8')).hexdigest()
    return plaintext, token_hash

_SECRET_FIELDS = ('password', 'twofa_secret')


def _strip_secrets(row: Optional[dict]) -> Optional[dict]:
    """Вырезать секреты из строки учётки (как { ...a, password_hash: undefined } в Express)."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in _SECRET_FIELDS}


def list_accounts(**params) -> dict:
    return repository.list_accounts(**params)


def get_account(account_id: int) -> Optional[dict]:
    """Учётка + teacher_name, без секретов. None если не найдена."""
    row = _strip_secrets(repository.get_by_id_with_teacher(account_id))
    if row is not None:
        row['name'] = row.get('full_name') or row.get('teacher_name') or row['email']
    return row


def create_account(data: dict, actor_account_id: int, request: Request) -> dict:
    """
    Создать учётку БЕЗ пароля + выпустить invite-ссылку.

    Возвращает {'error': 'email_taken'} если email занят, иначе
    {'id', 'email', 'role', 'teacher_id', 'invite_url', 'expires_at'}.
    Пароль НЕ генерируется — пользователь устанавливает его сам по invite.
    """
    email = data['email']
    role = data['role']
    teacher_id = data.get('teacher_id')

    if repository.find_by_email(email) is not None:
        return {'error': 'email_taken'}

    acc = repository.create_account(
        email=email, role=role, teacher_id=teacher_id, full_name=data.get('full_name'),
    )
    log_event(
        event='account_created',
        account_id=actor_account_id,
        target_id=acc['id'],
        meta={'email': email, 'role': role},
        request=request,
    )

    invite = issue_invite(acc['id'], actor_account_id, request)
    return {
        'id': acc['id'],
        'email': acc['email'],
        'role': acc['role'],
        'teacher_id': acc['teacher_id'],
        'full_name': acc.get('full_name'),
        'invite_url': invite['invite_url'],
        'expires_at': invite['expires_at'],
    }


def update_account(account_id: int, data: dict) -> Optional[dict]:
    """COALESCE-обновление email/role/active, без секретов. None если не найдена.

    Внимание: как и Express updateAccount, ответ НЕ содержит teacher_name
    (RETURNING * без JOIN). При смене email инкрементирует token_version."""
    row = repository.update_account(
        account_id,
        email=data.get('email'),
        role=data.get('role'),
        active=data.get('active'),
        full_name=data.get('full_name'),
    )
    if row is not None and data.get('email') is not None:
        repository.bump_token_version(account_id)
    return _strip_secrets(row)


def reset_password(account_id: int, actor_account_id: int, request: Request) -> Optional[dict]:
    """
    Сброс пароля через invite-ссылку. None если учётка не найдена.

    Вместо генерации temp-пароля выпускает новый invite (старые отзываются).
    Возвращает {'invite_url', 'expires_at'}.

    Немедленно инвалидирует активные сессии аккаунта (bump token_version) — админский
    сброс пароля разлогинивает пользователя сразу, не дожидаясь установки нового пароля
    по invite. Зеркалит reset_twofa.
    """
    acc = repository.get_by_id(account_id)
    if acc is None:
        return None
    # Безопасность: аннулируем СТАРЫЙ пароль (password_hash → NULL). Иначе вход по
    # прежнему паролю продолжал бы работать в обход invite-ссылки. Аккаунт переходит
    # в «приглашённое» состояние (без пароля) — войти можно только установив новый
    # пароль по invite (как только что созданная учётка). verify_password(NULL) → False.
    repository.set_password(account_id, None)
    repository.bump_token_version(account_id)
    log_event(
        event='password_reset', account_id=actor_account_id, target_id=acc['id'], request=request,
    )
    return issue_invite(account_id, actor_account_id, request)


def reset_twofa(account_id: int, actor_account_id: int, request: Request) -> bool:
    """Сброс 2FA + recovery-кодов. False если не найдена. Порт accounts.js reset-2fa."""
    acc = repository.reset_twofa(account_id)
    if acc is None:
        return False
    repository.bump_token_version(account_id)
    log_event(
        event='2fa_reset', account_id=actor_account_id, target_id=acc['id'], request=request,
    )
    return True


def issue_invite(account_id: int, actor_account_id: int, request: Request) -> Optional[dict]:
    """
    Выписать invite-ссылку для установки пароля.

    В транзакции:
      1. Отзываем все активные инвайты аккаунта (revoke старых).
      2. Генерируем plaintext-токен + SHA-256-хэш.
      3. Создаём запись в account_invites.
      4. Логируем invite_created.
    Возвращает {'invite_url': str, 'expires_at': datetime} или None если аккаунт не найден.
    Plaintext-токен НИКОГДА не сохраняется в БД — только SHA-256 hash.
    """
    acc = repository.get_by_id(account_id)
    if acc is None:
        return None

    expires_at = timezone.now() + datetime.timedelta(hours=INVITE_TTL_HOURS)
    plaintext, token_hash = generate_invite_token()

    with transaction.atomic():
        repository.revoke_active_for_account(account_id)
        repository.create_invite(
            account_id=account_id,
            token_hash=token_hash,
            created_by=actor_account_id,
            expires_at=expires_at,
        )
        log_event(
            event='invite_created',
            account_id=actor_account_id,
            target_id=account_id,
            meta={'email': acc['email'], 'role': acc['role']},
            request=request,
        )

    return {
        'invite_url': _invite_url(plaintext),
        'expires_at': expires_at,
    }


def regenerate_invite(account_id: int, actor_account_id: int, request: Request) -> Optional[dict]:
    """
    Перевыпустить invite-ссылку (отзывает старые, создаёт новую).
    None если учётка не найдена.
    Возвращает {'invite_url', 'expires_at'}.
    """
    return issue_invite(account_id, actor_account_id, request)


def revoke_invite(account_id: int, actor_account_id: int, request: Request) -> bool:
    """
    Отозвать все активные инвайты аккаунта.
    False если учётка не найдена. Логирует invite_revoked.
    """
    acc = repository.get_by_id(account_id)
    if acc is None:
        return False
    repository.revoke_active_for_account(account_id)
    log_event(
        event='invite_revoked',
        account_id=actor_account_id,
        target_id=account_id,
        meta={'email': acc['email']},
        request=request,
    )
    return True


def _invite_url(token: str) -> str:
    """Сформировать URL для установки пароля по invite-токену."""
    return f'/login/set-password?token={token}'


def soft_delete(account_id: int, actor_account_id: int, request: Request) -> bool:
    """Мягкое удаление. False если не найдена. Порт accounts.js DELETE."""
    ok = repository.soft_delete(account_id)
    if not ok:
        return False
    repository.bump_token_version(account_id)
    log_event(
        event='account_deactivated', account_id=actor_account_id,
        target_id=account_id, request=request,
    )
    return True


def hard_delete(account_id: int, actor_account_id, request: Request) -> bool:
    """Физическое удаление учётки. False если не найдена.

    Аудит-событие пишем ДО удаления строки — иначе target_id ссылался бы
    на уже несуществующий аккаунт в момент записи.
    """
    acc = repository.get_by_id(account_id)
    if acc is None:
        return False
    log_event(
        event='account_deleted', account_id=actor_account_id,
        target_id=account_id, meta={'email': acc['email'], 'role': acc['role']},
        request=request,
    )
    return repository.hard_delete(account_id)


def set_active(account_id: int, active: bool, actor_account_id, request: Request) -> bool:
    """Отключить/включить учётку (обратимо). False если не найдена."""
    acc = repository.get_by_id(account_id)
    if acc is None:
        return False
    repository.set_active(account_id, active)
    if not active:
        repository.bump_token_version(account_id)
    log_event(
        event='account_enabled' if active else 'account_disabled',
        account_id=actor_account_id, target_id=account_id, request=request,
    )
    return True
