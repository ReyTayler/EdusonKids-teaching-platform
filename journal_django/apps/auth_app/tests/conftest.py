"""
conftest для тестов auth_app. managed=False — test-БД eduson_test, чистим прямым DELETE.

Изменения Фазы 4 (architecture_v2.md):
  - Убраны _make_cookie / TEST_SECRET / session_cookie / HMAC (мёртвый механизм).
  - _reset_rate_limiter: теперь очищает Django cache (django-ratelimit хранит счётчики
    в cache, а не в in-memory _rate_store из старых views.py).
  - account_factory: raw INSERT исправлен с password_hash/active на password/is_active
    (AbstractUser, реальная схема accounts).
"""
from __future__ import annotations

import pytest
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import connection


# ---------------------------------------------------------------------------
# DB setup — session-scoped pass (managed=False, работаем с eduson_test)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def django_db_setup():
    pass


# ---------------------------------------------------------------------------
# Сброс rate-limiter между тестами
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """
    Сбрасывать кэш django-ratelimit перед и после каждого теста.

    django-ratelimit хранит счётчики в Django cache (по умолчанию LocMemCache).
    В test-настройках cache бэкенд — стандартный LocMem.
    """
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# Фабрика учёток с автоочисткой
# ---------------------------------------------------------------------------

@pytest.fixture
def account_factory():
    """
    Фабрика учёток с автоочисткой (recovery-коды + audit + учётка).

    Использует AbstractUser-колонки: password (не password_hash), is_active (не active).
    factory(email, role, teacher_id, password, twofa_method, twofa_secret, twofa_enabled, is_active)
    → account dict (полная строка из БД).
    """
    created: list[dict] = []

    def factory(
        email: str = '__auth__@example.com',
        role: str = 'teacher',
        teacher_id=None,
        password: str = 'secret123',
        twofa_method=None,
        twofa_secret=None,
        twofa_enabled: bool = False,
        is_active: bool = True,
    ) -> dict:
        owned_teacher_id = None
        # Хешируем пароль через Django hasher
        password_hashed = make_password(password)

        with connection.cursor() as cur:
            # Для teacher роли нужен teacher_id (CHECK-ограничение в БД)
            if role == 'teacher' and teacher_id is None:
                cur.execute(
                    "INSERT INTO teachers (name) VALUES ('__auth_teacher__') RETURNING id"
                )
                teacher_id = cur.fetchone()[0]
                owned_teacher_id = teacher_id

            cur.execute(
                'INSERT INTO accounts '
                '(email, password, role, teacher_id, twofa_method, twofa_secret, '
                'twofa_enabled, is_active, is_staff, is_superuser, first_name, last_name, date_joined) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false, false, \'\', \'\', NOW()) RETURNING *',
                [email, password_hashed, role, teacher_id,
                 twofa_method, twofa_secret, twofa_enabled, is_active],
            )
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            acc = dict(zip(cols, row))
            created.append({'id': acc['id'], 'owned_teacher_id': owned_teacher_id})
        return acc

    yield factory

    with connection.cursor() as cur:
        for item in created:
            acc_id = item['id']
            cur.execute('DELETE FROM account_invites WHERE account_id = %s', [acc_id])
            cur.execute('DELETE FROM account_recovery_codes WHERE account_id = %s', [acc_id])
            cur.execute(
                'DELETE FROM security_audit_log WHERE target_id = %s OR account_id = %s',
                [acc_id, acc_id],
            )
            cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])
            # Удалить teacher только если создавался этой фабрикой
            tid = item.get('owned_teacher_id')
            if tid:
                cur.execute('DELETE FROM teachers WHERE id = %s', [tid])
