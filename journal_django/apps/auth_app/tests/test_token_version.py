"""
test_token_version.py — инварианты token_version для CookieJWTAuthentication.

Инварианты (идентичны старому HMAC-cookie, механизм — JWT):
  - JWT с корректной token_version + активный аккаунт → 200
  - JWT с устаревшей token_version (bump_token_version → БД выросла) → 401
  - Неактивный аккаунт (is_active=False) → 401
  - soft_delete бампит token_version
  - challenge-токен (без claim token_version) НЕ аутентифицирует как полноценная сессия → 401

БД: реальная eduson_test через managed=False + django_db_setup=pass (из conftest).
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


# ---------------------------------------------------------------------------
# Фикстура: manager-аккаунт (AbstractUser: password + is_active)
# ---------------------------------------------------------------------------

@pytest.fixture
def manager_acc():
    """Создаёт manager-аккаунт. Возвращает объект Account."""
    from apps.accounts.models import Account
    from django.contrib.auth.hashers import make_password

    acc = Account.objects.create(
        email='__tv_jwt__@x.com',
        password=make_password('secret123'),
        role='manager',
        token_version=0,
        is_active=True,
    )
    yield acc

    # Teardown — прямой DELETE чтобы не полагаться на CASCADE в managed=False таблицах
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
            [acc.id, acc.id],
        )
    Account.objects.filter(pk=acc.pk).delete()


def _client_for(account) -> APIClient:
    """APIClient с JWT access-cookie для данного аккаунта."""
    refresh = RefreshToken.for_user(account)
    refresh['token_version'] = account.token_version
    client = APIClient()
    cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    client.cookies[cookie_name] = str(refresh.access_token)
    return client


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_matching_version_authenticates(manager_acc):
    """JWT с token_version=0, БД=0 → 200."""
    client = _client_for(manager_acc)
    r = client.get('/api/auth/me')
    assert r.status_code == 200


@pytest.mark.django_db
def test_stale_version_rejected(manager_acc):
    """
    bump_token_version → БД=1, JWT несёт 0 → 401.

    Выдаём токен ДО бампа (несёт version=0), потом бампим → mismatch.
    """
    from apps.accounts import repository

    # Токен выдаётся пока version=0
    client = _client_for(manager_acc)

    # Бампим — БД → 1
    repository.bump_token_version(manager_acc.id)

    r = client.get('/api/auth/me')
    assert r.status_code == 401


@pytest.mark.django_db
def test_inactive_account_rejected(manager_acc):
    """is_active=False → 401 (штатная simplejwt-проверка в get_user)."""
    client = _client_for(manager_acc)

    # Деактивируем напрямую через SQL (не через ORM, чтобы не ловить Django-сигналы)
    with connection.cursor() as cur:
        cur.execute('UPDATE accounts SET is_active = false WHERE id = %s', [manager_acc.id])

    r = client.get('/api/auth/me')
    assert r.status_code == 401


@pytest.mark.django_db
def test_soft_delete_bumps_version(manager_acc):
    """soft_delete бампит token_version → версия в БД растёт."""
    from apps.accounts import services, repository

    class _FakeRequest:
        META = {}

    services.soft_delete(manager_acc.id, actor_account_id=manager_acc.id, request=_FakeRequest())
    state = repository.get_auth_state(manager_acc.id)
    assert state['token_version'] == 1


@pytest.mark.django_db
def test_challenge_token_is_not_a_session(manager_acc):
    """
    Session Fixation: challenge-токен (AccessToken без claim token_version) НЕ
    должен аутентифицировать как полноценная сессия.

    CookieJWTAuthentication.get_user() проверяет наличие claim token_version →
    при его отсутствии → AuthenticationFailed → 401.
    """
    from rest_framework_simplejwt.tokens import AccessToken

    # Создаём "challenge" access-токен без token_version
    challenge = AccessToken()
    challenge['user_id'] = manager_acc.id
    # намеренно НЕ ставим challenge['token_version']

    client = APIClient()
    cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    client.cookies[cookie_name] = str(challenge)

    r = client.get('/api/auth/me')
    assert r.status_code == 401
