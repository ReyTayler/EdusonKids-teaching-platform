"""
services.py — оркестрация auth-логики.

Переписано на использование стандартного Django auth:
  • authenticate() вместо ручной проверки пароля
  • login()/logout() из django.contrib.auth
  • challenge-токены через django.core.signing
  • check_password вместо verify_password
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password, check_password
from django.core.signing import TimestampSigner, BadSignature
from django.utils import timezone


from apps.accounts import repository as accounts_repo
from apps.audit.services import log_event
from apps.auth_app import mailer
from apps.auth_app import twofa as twofa_module


def send_otp_email(to: str, code: str) -> None:
    """
    Диспатч OTP-письма (Celery-спека 2026-07-13, фаза A).

    С celery — фоновой задачей (SMTP не блокирует запрос /login; ретраи — в
    задаче). Без celery (локальный dev) — синхронно через mailer, как раньше.
    Импорт задач — в рантайме: пакет celery локально может быть не установлен
    (тот же паттерн, что config/__init__.py). Точка-шов для тестов: API-тесты
    мокают apps.auth_app.services.send_otp_email.
    """
    try:
        from apps.auth_app.tasks import send_otp_email_task
    except ImportError:
        mailer.send_otp_email(to, code)
        return
    send_otp_email_task.delay(to, code)


# Сообщение об ошибке входа — одинаковое для email/пароль/роли (порт FAIL)
FAIL = {'error': 'Неверный email или пароль'}

# TTL login-challenge: 5 минут в секундах
CHALLENGE_TTL = 5 * 60

# Подписант для challenge-токенов: TimestampSigner — даёт встроенный TTL
# через unsign_object(max_age=...) (голый Signer max_age не поддерживает).
_signer = TimestampSigner()


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

import hashlib

def hash_invite_token(token: str) -> str:
    """SHA-256 hex от plaintext invite-токена. Для поиска по hash в БД."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def role_matches(button_role: str, account_role: str) -> bool:
    """teacher-кнопка ↔ роль teacher; admin-кнопка ↔ manager|admin|superadmin."""
    if button_role == 'teacher':
        return account_role == 'teacher'
    return account_role in ('manager', 'admin', 'superadmin')


def redirect_for(role: str) -> str:
    """Целевой путь после входа."""
    return '/teacher' if role == 'teacher' else '/admin'


def requires_2fa(role: str) -> bool:
    """2FA обязательна для ВСЕХ ролей."""
    return True


def issue_challenge(account, stage: str) -> str:
    """
    Выдать login-challenge токен через django.core.signing.
    account — может быть dict или объект Account.
    """
    account_id = account['id'] if isinstance(account, dict) else account.id
    account_role = account['role'] if isinstance(account, dict) else account.role

    payload = {
        'kind': 'login_challenge',
        'stage': stage,
        'account_id': account_id,
        'role': account_role,
    }
    return _signer.sign_object(payload)


def read_challenge(token: Optional[str]) -> Optional[dict]:
    """
    Декодировать login-challenge через unsign_object.
    Возвращает payload если kind='login_challenge', иначе None.
    """
    if not token:
        return None
    try:
        p = _signer.unsign_object(token, max_age=CHALLENGE_TTL)
    except BadSignature:
        return None
    return p if (p and p.get('kind') == 'login_challenge') else None


# ---------------------------------------------------------------------------
# twofa_verify_challenge
# ---------------------------------------------------------------------------

def twofa_verify_challenge(acc) -> dict:
    """
    Ответ для аккаунта с УЖЕ включённой 2FA.
    acc — dict или объект Account.
    """
    acc_id = acc['id'] if isinstance(acc, dict) else acc.id
    acc_email = acc['email'] if isinstance(acc, dict) else acc.email
    acc_method = acc['twofa_method'] if isinstance(acc, dict) else acc.twofa_method

    if acc_method == 'email':
        result = twofa_module.issue_email_challenge(acc_id)
        send_otp_email(acc_email, result['code'])
        return {'twofa_required': True, 'method': 'email', 'challenge_token': result['challenge']}
    return {'twofa_required': True, 'method': 'totp', 'challenge_token': issue_challenge(acc, 'verify')}


# ---------------------------------------------------------------------------
# login — POST /login
# ---------------------------------------------------------------------------

def login(
    email: str,
    password: str,
    role: str,
    request=None,
) -> Tuple[dict, int, Optional[object]]:
    """
    Основной вход. Использует django.contrib.auth.authenticate.
    Возвращает (data, status, user) где user — объект Account.
    """
    # Используем стандартный authenticate
    user = authenticate(request, email=email, password=password)
    if user is None:
        # Пытаемся найти аккаунт по email, чтобы зафиксировать failed attempt
        acc = accounts_repo.find_by_email(email)
        if acc:
            accounts_repo.register_login_failure(acc['id'])  # ← блокировка при переборе
        account_id = acc['id'] if acc else None
        log_event('login_fail', account_id=account_id, actor_email=email, request=request)
        return FAIL, 401, None

    if not user.is_active:
        log_event('login_fail', actor_email=email, meta={'reason': 'disabled'}, request=request)
        return FAIL, 401, None

    # Проверка локаута
    if user.locked_until and _is_locked(user.locked_until):
        log_event('locked', account_id=user.id, actor_email=email, request=request)
        return {'error': 'Временно заблокировано, попробуйте позже'}, 429, None

    # Проверка роли
    if not role_matches(role, user.role):
        accounts_repo.register_login_failure(user.id)
        log_event('login_fail', account_id=user.id, actor_email=email,
                  meta={'reason': 'role'}, request=request)
        return FAIL, 401, None

    # 2FA уже включена
    if user.twofa_enabled:
        return twofa_verify_challenge(user), 200, None

    # Требуется настройка 2FA
    if requires_2fa(user.role):
        return {
            'twofa_enrollment_required': True,
            'challenge_token': issue_challenge(user, 'enroll'),
        }, 200, None

    accounts_repo.register_login_success(user.id)
    log_event('login_success', account_id=user.id, actor_email=email, request=request)
    return {'role': user.role, 'redirect': redirect_for(user.role)}, 200, user


# ---------------------------------------------------------------------------
# login_2fa — POST /login/2fa
# ---------------------------------------------------------------------------

def login_2fa(
    challenge_token: str,
    code: str,
    request=None,
) -> Tuple[dict, int, Optional[object]]:
    """
    Завершение входа по 2FA-коду.
    """
    # Декодируем challenge
    raw = read_challenge(challenge_token)
    candidate_id = raw['account_id'] if (raw and raw.get('account_id')) else None

    if candidate_id:
        acc0 = accounts_repo.get_by_id(candidate_id)
        if acc0 and acc0.get('locked_until') and _is_locked(acc0['locked_until']):
            return {'error': 'Временно заблокировано, попробуйте позже'}, 429, None

    # Попытка email-challenge
    email_res = twofa_module.verify_email_challenge(challenge_token, code)
    account_id = email_res['account_id'] if email_res.get('ok') else None
    via_recovery = False

    if not account_id:
        ch = raw if (raw and raw.get('kind') == 'login_challenge') else None
        if ch and ch.get('stage') == 'verify':
            acc = accounts_repo.get_by_id(ch['account_id'])
            if acc:
                if acc.get('twofa_method') == 'totp' and twofa_module.verify_totp(
                    acc.get('twofa_secret'), code
                ):
                    account_id = acc['id']
                else:
                    for rc in accounts_repo.list_recovery_codes(acc['id']):
                        if not rc.get('used_at') and twofa_module.verify_recovery(
                            code, rc.get('code_hash')
                        ):
                            accounts_repo.mark_recovery_used(rc['id'])
                            account_id = acc['id']
                            via_recovery = True
                            break

    if not account_id:
        if candidate_id:
            accounts_repo.register_login_failure(candidate_id)
        log_event('2fa_fail', account_id=candidate_id, request=request)
        return {'error': 'Неверный или просроченный код'}, 401, None

    acc = accounts_repo.get_by_id(account_id)
    accounts_repo.register_login_success(acc['id'])
    log_event(
        'login_success',
        account_id=acc['id'],
        actor_email=acc['email'],
        meta={'viaRecovery': via_recovery},
        request=request,
    )
    # Возвращаем account_id, чтобы view мог получить объект User
    from apps.accounts.models import Account
    user = Account.objects.get(id=acc['id'])
    return {'role': acc['role'], 'redirect': redirect_for(acc['role'])}, 200, user


# ---------------------------------------------------------------------------
# me — GET /me
# ---------------------------------------------------------------------------

def me(account_id: int) -> Tuple[dict, int]:
    """Текущий аккаунт."""
    acc = accounts_repo.get_by_id_with_teacher(account_id)
    if not acc:
        return {'error': 'Unauthorized'}, 401
    return {
        'account_id': acc['id'],
        'email': acc['email'],
        'role': acc['role'],
        'teacher_id': acc.get('teacher_id'),
        'name': acc.get('full_name') or acc.get('teacher_name') or acc['email'],
        'twofa_enabled': acc.get('twofa_enabled', False),
    }, 200


# ---------------------------------------------------------------------------
# twofa_setup — POST /2fa/setup
# ---------------------------------------------------------------------------

def twofa_setup(
    challenge_token: Optional[str],
    method: str,
    request=None,
) -> Tuple[dict, int]:
    """Enrollment: настройка метода 2FA."""
    ch = read_challenge(challenge_token)
    if not ch or ch.get('stage') != 'enroll':
        return {'error': 'Сессия входа истекла'}, 401

    acc = accounts_repo.get_by_id(ch['account_id'])
    if not acc:
        return {'error': 'Сессия входа истекла'}, 401

    if method == 'totp':
        secret_key = twofa_module.generate_secret()
        accounts_repo.set_twofa(acc['id'], 'totp', secret_key, enabled=False, confirmed=False)
        uri = twofa_module.provisioning_uri(secret_key, acc['email'])
        return {
            'method': 'totp',
            'secret': secret_key,
            'qr': twofa_module.qr_data_url(uri),
        }, 200

    # email
    accounts_repo.set_twofa(acc['id'], 'email', None, enabled=False, confirmed=False)
    result = twofa_module.issue_email_challenge(acc['id'])
    send_otp_email(acc['email'], result['code'])
    return {'method': 'email', 'challenge_token': result['challenge']}, 200


# ---------------------------------------------------------------------------
# twofa_enable — POST /2fa/enable
# ---------------------------------------------------------------------------

def twofa_enable(
    challenge_token: Optional[str],
    code: str,
    request=None,
) -> Tuple[dict, int, Optional[object]]:
    """Enrollment: подтвердить код, включить 2FA, выдать recovery-коды."""
    raw = read_challenge(challenge_token)
    candidate_id = raw['account_id'] if (raw and raw.get('account_id')) else None

    acc = None
    ok = False
    email_res = twofa_module.verify_email_challenge(challenge_token, code)
    if email_res.get('ok'):
        acc = accounts_repo.get_by_id(email_res['account_id'])
        ok = bool(acc and acc.get('twofa_method') == 'email')
    elif raw and raw.get('kind') == 'login_challenge' and raw.get('stage') == 'enroll':
        acc = accounts_repo.get_by_id(raw['account_id'])
        if acc and acc.get('twofa_method') == 'totp':
            ok = twofa_module.verify_totp(acc.get('twofa_secret'), code)

    if not acc or not ok:
        if candidate_id:
            accounts_repo.register_login_failure(candidate_id)
        log_event('2fa_fail', account_id=candidate_id, request=request)
        return {'error': 'Неверный или просроченный код'}, 401, None

    accounts_repo.set_twofa(
        acc['id'],
        acc.get('twofa_method'),
        acc.get('twofa_secret'),
        enabled=True,
        confirmed=True,
    )
    accounts_repo.bump_token_version(acc['id'])
    rc = twofa_module.generate_recovery_codes()
    accounts_repo.replace_recovery_codes(acc['id'], rc['hashes'])
    accounts_repo.register_login_success(acc['id'])
    log_event('2fa_enabled', account_id=acc['id'], actor_email=acc['email'], request=request)

    from apps.accounts.models import Account
    user = Account.objects.get(id=acc['id'])
    return {
        'role': user.role,
        'redirect': redirect_for(user.role),
        'recovery_codes': rc['plain'],
    }, 200, user


# ---------------------------------------------------------------------------
# twofa_disable — POST /2fa/disable
# ---------------------------------------------------------------------------

def twofa_disable(account_id: int, password: str, request=None) -> Tuple[dict, int]:
    """Выключить 2FA. Требует пароля."""
    acc = accounts_repo.get_by_id(account_id)
    if not acc:
        return {'error': 'Unauthorized'}, 401
    # Используем check_password из django.contrib.auth.hashers
    if not check_password(password, acc.get('password')):
        return {'error': 'Неверный пароль'}, 401
    accounts_repo.reset_twofa(acc['id'])
    accounts_repo.bump_token_version(acc['id'])
    log_event('2fa_disabled', account_id=acc['id'], request=request)
    return {'ok': True}, 200


# ---------------------------------------------------------------------------
# email_send — POST /2fa/email/send
# ---------------------------------------------------------------------------

def email_send(challenge_token: str, request=None) -> Tuple[dict, int]:
    """Повторно отправить email-код."""
    ch = read_challenge(challenge_token)
    if not ch:
        return {'error': 'Сессия входа истекла'}, 401
    acc = accounts_repo.get_by_id(ch['account_id'])
    if not acc or acc.get('twofa_method') != 'email':
        return {'error': 'Метод недоступен'}, 400
    result = twofa_module.issue_email_challenge(acc['id'])
    send_otp_email(acc['email'], result['code'])
    return {'challenge_token': result['challenge']}, 200


# ---------------------------------------------------------------------------
# invite_lookup — GET /invite
# ---------------------------------------------------------------------------

def invite_lookup(token: str) -> Tuple[dict, int]:
    """Проверить invite-токен."""
    if not token:
        return {'valid': False}, 200
    inv = accounts_repo.find_active_by_hash(hash_invite_token(token))
    if inv is None:
        return {'valid': False}, 200
    acc = accounts_repo.get_by_id(inv['account_id'])
    if acc is None:
        return {'valid': False}, 200
    return {'valid': True, 'email': acc['email'], 'role': acc['role']}, 200


# ---------------------------------------------------------------------------
# invite_accept — POST /invite/accept
# ---------------------------------------------------------------------------

def invite_accept(
    token: str,
    password: str,
    request=None,
) -> Tuple[dict, int, Optional[object]]:
    """Принять invite: установить пароль."""
    inv = accounts_repo.find_active_by_hash(hash_invite_token(token))
    if inv is None:
        log_event('invite_accept_fail', request=request)
        return {'error': 'Ссылка недействительна'}, 400, None

    # Используем make_password из django.contrib.auth.hashers
    res = accounts_repo.accept_invite(inv['id'], make_password(password))
    if res is None:
        log_event('invite_accept_fail', account_id=inv['account_id'], request=request)
        return {'error': 'Ссылка недействительна'}, 400, None

    acc = accounts_repo.get_by_id(inv['account_id'])
    log_event(
        'invite_used',
        account_id=inv['account_id'],
        actor_email=acc['email'] if acc else None,
        request=request,
    )

    if acc and acc.get('twofa_enabled'):
        return twofa_verify_challenge(acc), 200, None
    return {
        'twofa_enrollment_required': True,
        'challenge_token': issue_challenge(acc, 'enroll'),
    }, 200, None


# ---------------------------------------------------------------------------
# Внутренние утилиты
# ---------------------------------------------------------------------------

def _is_locked(locked_until) -> bool:
    if locked_until is None:
        return False
    # Предполагаем, что locked_until — datetime из БД (с учётом USE_TZ)
    return locked_until > timezone.now()