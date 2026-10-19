"""
conftest для тестов accounts.

Фаза 4: HMAC _make_cookie / TEST_SECRET / session_cookie удалены.
Аутентификация — JWT через корневые фикстуры (admin_client).
account_factory исправлен: password (не password_hash), is_active (не active).
managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection


@pytest.fixture(scope='session')
def django_db_setup():
    pass


# ---------------------------------------------------------------------------
# Фикстуры аккаунтов
# ---------------------------------------------------------------------------

@pytest.fixture
def teacher_fixture():
    """Выделенный тестовый учитель (для учёток role=teacher)."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__acc_teacher__') RETURNING id")
        tid = cur.fetchone()[0]
    yield tid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [tid])


@pytest.fixture
def account_factory():
    """
    Фабрика учёток с автоочисткой (recovery-коды + audit + сама учётка).

    factory(email=..., role='manager', teacher_id=None, twofa=False) → account_id.

    Использует AbstractUser-колонки: password (хешированный), is_active.
    """
    created: list[int] = []

    def factory(
        email: str = '__acc__@example.com',
        role: str = 'manager',
        teacher_id=None,
        twofa: bool = False,
        is_active: bool = True,
    ) -> int:
        pw = make_password('testpass123')
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO accounts (email, password, role, teacher_id, '
                'twofa_method, twofa_secret, twofa_enabled, is_active, is_staff, is_superuser, '
                'first_name, last_name, date_joined) '
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false, false, '', '', NOW()) RETURNING id",
                [
                    email, pw, role, teacher_id,
                    'totp' if twofa else None,
                    'SECRETSEED' if twofa else None,
                    twofa,
                    is_active,
                ],
            )
            acc_id = cur.fetchone()[0]
            if twofa:
                cur.execute(
                    'INSERT INTO account_recovery_codes (account_id, code_hash) VALUES (%s, %s)',
                    [acc_id, 'hash1'],
                )
        created.append(acc_id)
        return acc_id

    yield factory

    with connection.cursor() as cur:
        for acc_id in created:
            cur.execute('DELETE FROM account_invites WHERE account_id = %s', [acc_id])
            cur.execute('DELETE FROM account_recovery_codes WHERE account_id = %s', [acc_id])
            cur.execute('DELETE FROM security_audit_log WHERE target_id = %s OR account_id = %s',
                        [acc_id, acc_id])
            cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])


@pytest.fixture
def cleanup_email():
    """Удаляет учётку по email после теста (для POST-создания)."""
    emails: list[str] = []
    yield emails
    with connection.cursor() as cur:
        for email in emails:
            cur.execute('SELECT id FROM accounts WHERE email = %s', [email])
            row = cur.fetchone()
            if row:
                aid = row[0]
                cur.execute('DELETE FROM account_invites WHERE account_id = %s', [aid])
                cur.execute('DELETE FROM account_recovery_codes WHERE account_id = %s', [aid])
                cur.execute('DELETE FROM security_audit_log WHERE target_id = %s OR account_id = %s',
                            [aid, aid])
                cur.execute('DELETE FROM accounts WHERE id = %s', [aid])
