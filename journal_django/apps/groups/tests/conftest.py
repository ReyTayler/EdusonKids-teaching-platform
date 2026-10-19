"""
conftest.py для тестов groups.

Фаза 4: аутентификация через JWT (access-cookie), не HMAC session-cookie.
Фикстуры admin_account / teacher_account создают реальные аккаунты,
jwt_client_for возвращает APIClient с валидным access-токеном.

managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.hashers import make_password
from django.conf import settings
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


# ---------------------------------------------------------------------------
# Reuse production DB — не создавать test-базу
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def django_db_setup():
    """No-op: таблицы managed=False, управляем ими вручную в eduson_test."""
    pass


# ---------------------------------------------------------------------------
# JWT helper
# ---------------------------------------------------------------------------

def _jwt_client(account_id: int, token_version: int = 0) -> APIClient:
    """
    Создать APIClient с JWT access-cookie для аккаунта из БД.
    Использует Account ORM-модель.
    """
    from apps.accounts.models import Account
    user = Account.objects.get(pk=account_id)
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version
    client = APIClient()
    cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    client.cookies[cookie_name] = str(refresh.access_token)
    return client


# ---------------------------------------------------------------------------
# Фикстуры аккаунтов (реальные строки в eduson_test)
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_account():
    """Создаёт admin-аккаунт для API-тестов. Возвращает id."""
    pw = make_password('testpass123')
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (email, password, role, is_active, is_staff, is_superuser, first_name, last_name, token_version, date_joined) "
            "VALUES ('__grp_admin__@test.local', %s, 'admin', true, false, false, '', '', 0, NOW()) RETURNING id",
            [pw],
        )
        acc_id = cur.fetchone()[0]
    yield acc_id
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
            [acc_id, acc_id],
        )
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])


@pytest.fixture
def superadmin_account():
    """Создаёт superadmin-аккаунт для API-тестов. Возвращает id."""
    pw = make_password('testpass123')
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (email, password, role, is_active, is_staff, is_superuser, first_name, last_name, token_version, date_joined) "
            "VALUES ('__grp_super__@test.local', %s, 'superadmin', true, true, true, '', '', 0, NOW()) RETURNING id",
            [pw],
        )
        acc_id = cur.fetchone()[0]
    yield acc_id
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
            [acc_id, acc_id],
        )
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])


@pytest.fixture
def manager_account():
    """Создаёт manager-аккаунт для API-тестов. Возвращает id."""
    pw = make_password('testpass123')
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (email, password, role, is_active, is_staff, is_superuser, first_name, last_name, token_version, date_joined) "
            "VALUES ('__grp_mgr__@test.local', %s, 'manager', true, false, false, '', '', 0, NOW()) RETURNING id",
            [pw],
        )
        acc_id = cur.fetchone()[0]
    yield acc_id
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
            [acc_id, acc_id],
        )
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])


@pytest.fixture
def teacher_account():
    """Создаёт teacher-аккаунт для API-тестов. Возвращает id."""
    pw = make_password('testpass123')
    with connection.cursor() as cur:
        # teacher требует teacher_id
        cur.execute("INSERT INTO teachers (name) VALUES ('__grp_tch__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, is_superuser, first_name, last_name, token_version, date_joined) "
            "VALUES ('__grp_tch__@test.local', %s, 'teacher', %s, true, false, false, '', '', 0, NOW()) RETURNING id",
            [pw, teacher_id],
        )
        acc_id = cur.fetchone()[0]
    yield acc_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


# ---------------------------------------------------------------------------
# Client-фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def anon_client():
    """APIClient без аутентификации."""
    return APIClient()


@pytest.fixture
def admin_client(admin_account):
    """APIClient с JWT для admin."""
    return _jwt_client(admin_account)


@pytest.fixture
def superadmin_client(superadmin_account):
    """APIClient с JWT для superadmin."""
    return _jwt_client(superadmin_account)


@pytest.fixture
def manager_client(manager_account):
    """APIClient с JWT для manager."""
    return _jwt_client(manager_account)


@pytest.fixture
def teacher_client(teacher_account):
    """APIClient с JWT для teacher."""
    return _jwt_client(teacher_account)
