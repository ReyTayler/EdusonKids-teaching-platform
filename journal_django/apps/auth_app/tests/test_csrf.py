"""
test_csrf.py — выдача csrftoken-cookie и enforcement CSRF на мутациях.

GET /api/auth/csrf (@ensure_csrf_cookie) выставляет csrftoken-cookie для SPA.
CookieJWTAuthentication на мутациях авторизованного клиента требует X-CSRFToken.

CSRF в тестах: дефолтный APIClient ставит _dont_enforce_csrf_checks → CSRF НЕ
проверяется. Для проверки самого механизма берём APIClient(enforce_csrf_checks=True).

БД: реальная eduson_test через managed=False + django_db_setup=pass (из conftest).
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def manager_acc():
    """Создаёт manager-аккаунт. Возвращает объект Account."""
    from apps.accounts.models import Account

    acc = Account.objects.create(
        email='__csrf_jwt__@x.com',
        password=make_password('secret123'),
        role='manager',
        token_version=0,
        is_active=True,
    )
    yield acc

    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
            [acc.id, acc.id],
        )
    from apps.accounts.models import Account as _A
    _A.objects.filter(pk=acc.pk).delete()


def _csrf_jwt_client(account) -> APIClient:
    """APIClient с JWT access-cookie И включённой CSRF-проверкой."""
    refresh = RefreshToken.for_user(account)
    refresh['token_version'] = account.token_version
    client = APIClient(enforce_csrf_checks=True)
    cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    client.cookies[cookie_name] = str(refresh.access_token)
    return client


# ---------------------------------------------------------------------------
# 1. Endpoint выставляет cookie
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_csrf_endpoint_sets_cookie():
    """GET /api/auth/csrf (анонимно) → 204 + csrftoken-cookie в ответе."""
    client = APIClient(enforce_csrf_checks=True)
    r = client.get('/api/auth/csrf')
    assert r.status_code == 204
    assert r.cookies.get('csrftoken') is not None
    assert r.cookies['csrftoken'].value != ''
    # SPA читает токен из JS → cookie обязана быть НЕ HttpOnly.
    # Регрессия CSRF_COOKIE_HTTPONLY=True молча сломала бы фронт — ловим здесь.
    assert not r.cookies['csrftoken']['httponly']


# ---------------------------------------------------------------------------
# 2. Мутация без X-CSRFToken отклоняется
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_mutation_without_csrf_token_forbidden(manager_acc):
    """
    Авторизованный POST без X-CSRFToken → 403.

    manager имеет право на POST /api/admin/groups (IsManagerOrAdmin), значит
    единственная причина 403 здесь — провал CSRF в CookieJWTAuthentication.
    custom_exception_handler переписывает тело PermissionDenied в
    {'error':'Forbidden'} (деталь «CSRF Failed» скрыта), поэтому проверяем код.
    test_mutation_with_csrf_token_passes подтверждает, что тот же аккаунт+эндпоинт
    с валидным токеном 403 НЕ возвращает — пара тестов изолирует CSRF как причину.
    """
    client = _csrf_jwt_client(manager_acc)
    r = client.post('/api/admin/groups', {'name': 'x'}, format='json')
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 3. Мутация с валидным X-CSRFToken проходит CSRF-проверку
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_mutation_with_csrf_token_passes(manager_acc):
    """С csrftoken-cookie + совпадающим X-CSRFToken мутация НЕ падает на CSRF."""
    client = _csrf_jwt_client(manager_acc)

    # 1) получаем csrftoken-cookie штатным endpoint'ом
    client.get('/api/auth/csrf')
    token = client.cookies['csrftoken'].value
    assert token

    # 2) шлём токен заголовком — CSRF должен пройти (далее уже бизнес-логика, не 403)
    r = client.post(
        '/api/admin/groups',
        {'name': '__csrf_probe__'},
        format='json',
        HTTP_X_CSRFTOKEN=token,
    )
    assert r.status_code != 403, f'CSRF не прошёл: {r.status_code} {getattr(r, "data", None)}'

    # cleanup, если группа реально создалась (201)
    if r.status_code == 201:
        gid = r.data.get('id')
        if gid:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM groups WHERE id = %s', [gid])


# ---------------------------------------------------------------------------
# 4. Регрессия: остаточная access-cookie не должна ломать анонимный вход
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_login_not_blocked_by_stale_access_cookie(manager_acc):
    """
    Точки входа (AllowAny) имеют authentication_classes=[] → остаточная
    валидная access-cookie НЕ навязывает им CSRF-проверку.

    Раньше POST /login со старой access-cookie без X-CSRFToken давал 403
    (CookieJWTAuthentication enforce CSRF до проверки креды). Теперь вход
    ведёт себя как без cookie: неверные креды → 401, а НЕ 403.
    """
    refresh = RefreshToken.for_user(manager_acc)
    refresh['token_version'] = manager_acc.token_version
    client = APIClient(enforce_csrf_checks=True)
    client.cookies['access'] = str(refresh.access_token)

    r = client.post(
        '/api/auth/login',
        {'email': 'definitely-not-real@x.com', 'password': 'wrong', 'role': 'admin'},
        format='json',
    )
    assert r.status_code != 403, f'регрессия: вход 403 из-за остаточной cookie ({r.status_code})'
    assert r.status_code == 401
