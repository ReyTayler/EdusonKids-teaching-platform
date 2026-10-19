"""
test_bootstrap_command.py — TDD-тесты для команды bootstrap_admin (Task 5.2, Task 12).

Покрывает:
  - создаёт superadmin без пароля + печатает invite-URL с '/login/set-password?token='
  - --if-empty: пропускает если уже есть superadmin-учётка (idempotent)

Task 12: доступ к Учёткам/Журналу ИБ/Зарплате теперь только у роли superadmin,
поэтому bootstrap_admin обязан создавать именно superadmin, иначе после
свежего разворачивания некому будет управлять доступами.
"""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def cleanup_admin():
    """Удаляет созданные в тесте admin-учётки по email."""
    emails: list[str] = []
    yield emails
    with connection.cursor() as cur:
        for e in emails:
            cur.execute('SELECT id FROM accounts WHERE email=%s', [e])
            row = cur.fetchone()
            if row:
                aid = row[0]
                cur.execute('DELETE FROM account_invites WHERE account_id=%s', [aid])
                cur.execute(
                    'DELETE FROM security_audit_log WHERE target_id=%s OR account_id=%s',
                    [aid, aid],
                )
                cur.execute('DELETE FROM accounts WHERE id=%s', [aid])


@pytest.mark.django_db
def test_bootstrap_creates_superadmin_and_prints_invite(cleanup_admin, settings):
    """
    bootstrap_admin --email=… создаёт superadmin БЕЗ пароля и печатает invite-URL
    вида '/login/set-password?token=…'.
    """
    settings.ADMIN_COOKIE_SECRET = 'deadbeef' * 16
    cleanup_admin.append('__boot__@x.com')
    out = StringIO()
    call_command('bootstrap_admin', '--email=__boot__@x.com', stdout=out)
    output = out.getvalue()
    assert '/login/set-password?token=' in output
    # Проверяем БД: role=superadmin, пароль не задан. После миграции на AbstractUser
    # колонка называется password и NOT NULL — create_account кладёт '' (пустой,
    # невалидный хеш → войти нельзя до приёма invite).
    with connection.cursor() as cur:
        cur.execute(
            "SELECT role, password FROM accounts WHERE email='__boot__@x.com'"
        )
        row = cur.fetchone()
    assert row is not None, 'Учётка не создана'
    role, pw = row
    assert role == 'superadmin'
    assert not pw  # '' — пароль не установлен


@pytest.mark.django_db
def test_if_empty_skips_when_admin_exists(cleanup_admin, settings):
    """
    --if-empty: если уже есть superadmin-учётка, команда выводит сообщение
    о пропуске и НЕ создаёт новую учётку.
    """
    settings.ADMIN_COOKIE_SECRET = 'deadbeef' * 16
    # Создаём существующего superadmin напрямую в БД
    cleanup_admin.append('__existing_admin__@x.com')
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts "
            "(email, password, role, teacher_id, is_active, is_staff, is_superuser, "
            " first_name, last_name, date_joined, token_version) "
            "VALUES ('__existing_admin__@x.com', '', 'superadmin', NULL, true, false, false, "
            " '', '', NOW(), 0) RETURNING id"
        )

    out = StringIO()
    call_command(
        'bootstrap_admin', '--email=__boot2__@x.com', '--if-empty', stdout=out
    )
    output = out.getvalue()
    assert 'пропуск' in output
    # Учётка __boot2__ НЕ создана
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE email='__boot2__@x.com'")
        assert cur.fetchone() is None


@pytest.mark.django_db
def test_bootstrap_creates_superadmin(cleanup_admin):
    """
    Task 12: bootstrap_admin обязан создавать роль superadmin, а не admin —
    иначе после свежего разворачивания некому будет управлять
    Учётками/Журналом ИБ/Зарплатой (доступ туда только у superadmin).
    """
    cleanup_admin.append('__boot_super__@example.com')
    call_command('bootstrap_admin', '--email=__boot_super__@example.com')
    with connection.cursor() as cur:
        cur.execute(
            "SELECT role FROM accounts WHERE email='__boot_super__@example.com'"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 'superadmin'
