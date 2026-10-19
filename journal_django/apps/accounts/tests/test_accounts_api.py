"""
E2E тесты для /api/admin/accounts (DRF APIClient, реальная БД managed=False).

Фокус безопасности: superadmin-only (manager/admin/teacher → 403), НИ ОДИН ответ не
содержит password_hash / twofa_secret, мутации логируются в security_audit_log с
санитизацией.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Account

pytestmark = pytest.mark.django_db

BASE = '/api/admin/accounts'

# Shared superadmin аккаунт для тестов, которые вызывают _client('superadmin') без actor_id.
# Создаётся один раз в _shared_superadmin_account, живёт весь модуль, удаляется в teardown.
_SHARED_SUPERADMIN_ID: int | None = None


@pytest.fixture(autouse=True)
def _shared_superadmin_account():
    """Создаём shared superadmin аккаунт для каждого теста — для вызовов _client('superadmin')."""
    global _SHARED_SUPERADMIN_ID  # noqa: PLW0603
    with connection.cursor() as cur:
        # Используем ON CONFLICT чтобы не падать если предыдущий тест не почистил
        cur.execute(
            "INSERT INTO accounts (email, password, role, is_active, is_staff, is_superuser, "
            "first_name, last_name, date_joined, token_version) "
            "VALUES (%s, %s, %s, true, false, false, '', '', NOW(), 0) "
            'ON CONFLICT (email) DO UPDATE SET is_active=true, token_version=0 '
            'RETURNING id',
            ['__shared_superadmin__@example.com', make_password('testpass123'), 'superadmin'],
        )
        _SHARED_SUPERADMIN_ID = cur.fetchone()[0]
    yield
    # Teardown — только инвайты и аудит, сам аккаунт оставляем для ON CONFLICT
    with connection.cursor() as cur:
        cur.execute('DELETE FROM account_invites WHERE account_id = %s', [_SHARED_SUPERADMIN_ID])
        cur.execute('DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
                    [_SHARED_SUPERADMIN_ID, _SHARED_SUPERADMIN_ID])
    _SHARED_SUPERADMIN_ID = None


def _jwt_client_for(account_id: int) -> APIClient:
    """Создаёт JWT-клиент для конкретного аккаунта."""
    account = Account.objects.get(pk=account_id)
    c = APIClient()
    refresh = RefreshToken.for_user(account)
    refresh['token_version'] = account.token_version
    c.cookies['access'] = str(refresh.access_token)
    return c


def _client(role: str | None, account_id: int | None = None) -> APIClient:
    """
    Создаёт APIClient с JWT-аутентификацией.

    Если role=None — анонимный клиент.
    Если account_id задан — JWT для этого аккаунта.
    Если нет account_id и role='superadmin' — использует _SHARED_SUPERADMIN_ID.
    """
    if role is None:
        return APIClient()
    if account_id is not None:
        return _jwt_client_for(account_id)
    if role == 'superadmin':
        assert _SHARED_SUPERADMIN_ID is not None, '_shared_superadmin_account не был создан'
        return _jwt_client_for(_SHARED_SUPERADMIN_ID)
    raise ValueError(f'_client({role!r}) без account_id не поддерживается (только superadmin)')


def _assert_no_secrets(obj):
    """Рекурсивно убедиться, что секреты не утекли."""
    if isinstance(obj, dict):
        assert 'password_hash' not in obj
        assert 'twofa_secret' not in obj
        for v in obj.values():
            _assert_no_secrets(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_secrets(v)


# ---------------------------------------------------------------------------
# Auth — superadmin-only
# ---------------------------------------------------------------------------

def test_no_cookie_401(anon_client):
    assert anon_client.get(BASE).status_code == 401


def test_teacher_forbidden(teacher_client):
    assert teacher_client.get(BASE).status_code == 403


def test_manager_forbidden(manager_client):
    # accounts — superadmin-only: даже manager получает 403.
    assert manager_client.get(BASE).status_code == 403


def test_admin_forbidden(admin_client):
    # КРИТИЧНО: accounts — superadmin-only, admin (не superadmin) получает 403.
    assert admin_client.get(BASE).status_code == 403


def test_superadmin_only(manager_client, admin_client, superadmin_client):
    for c in (manager_client, admin_client):
        assert c.get(BASE).status_code == 403
    resp = superadmin_client.get(BASE)
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {'rows', 'total', 'page', 'page_size'}


def test_admin_list_ok(account_factory):
    account_factory(email='__acc_api_list__@example.com')
    resp = _client('superadmin').get(BASE, {'filter[email]': '__acc_api_list__'})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {'rows', 'total', 'page', 'page_size'}
    _assert_no_secrets(body)


# ---------------------------------------------------------------------------
# GET /:id
# ---------------------------------------------------------------------------

def test_get_detail_no_secrets(teacher_fixture, account_factory):
    acc_id = account_factory(email='__acc_get__@example.com', role='teacher',
                             teacher_id=teacher_fixture, twofa=True)
    resp = _client('superadmin').get(f'{BASE}/{acc_id}')
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_secrets(body)
    assert body['email'] == '__acc_get__@example.com'
    assert body['teacher_name'] == '__acc_teacher__'
    # twofa_enabled видно, но secret — нет
    assert body['twofa_enabled'] is True


def test_get_detail_404():
    resp = _client('superadmin').get(f'{BASE}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


# ---------------------------------------------------------------------------
# POST create
# ---------------------------------------------------------------------------

def test_create_returns_password_no_secrets(account_factory, cleanup_email):
    # Создаём реального actor-аккаунта — stateful-auth пройдёт (active, token_version=0)
    # и FK аудита не сломается.
    actor_id = account_factory(email='__actor_create__@example.com', role='superadmin')
    cleanup_email.append('__acc_create__@example.com')
    resp = _client('superadmin', account_id=actor_id).post(
        BASE, {'email': '__acc_create__@example.com', 'role': 'manager'}, format='json'
    )
    assert resp.status_code == 201
    body = resp.json()
    # Новый контракт: invite_url + expires_at вместо password
    assert set(body.keys()) == {'id', 'email', 'role', 'teacher_id', 'full_name', 'invite_url', 'expires_at'}
    assert body['email'] == '__acc_create__@example.com'
    assert body['invite_url'].startswith('/login/set-password?token=')
    assert body['expires_at']
    _assert_no_secrets(body)
    # audit-событие account_created записано, без секретов в meta
    with connection.cursor() as cur:
        cur.execute(
            "SELECT meta FROM security_audit_log WHERE event='account_created' AND target_id=%s",
            [body['id']],
        )
        row = cur.fetchone()
    assert row is not None
    meta = row[0]
    assert 'password' not in meta and 'password_hash' not in meta
    assert meta['email'] == '__acc_create__@example.com'


def test_create_email_normalized_lowercase(cleanup_email):
    cleanup_email.append('__acc_upper__@example.com')
    resp = _client('superadmin').post(
        BASE, {'email': '  __ACC_UPPER__@Example.com  ', 'role': 'manager'}, format='json'
    )
    assert resp.status_code == 201
    assert resp.json()['email'] == '__acc_upper__@example.com'


def test_create_duplicate_email_409(account_factory):
    account_factory(email='__acc_dup__@example.com', role='manager')
    resp = _client('superadmin').post(
        BASE, {'email': '__acc_dup__@example.com', 'role': 'manager'}, format='json'
    )
    assert resp.status_code == 409
    assert resp.json() == {'error': 'Email уже используется'}


def test_create_teacher_requires_teacher_id():
    # role=teacher без teacher_id → 400 (refine).
    resp = _client('superadmin').post(BASE, {'email': '__x__@example.com', 'role': 'teacher'}, format='json')
    assert resp.status_code == 400


def test_create_non_teacher_with_teacher_id(teacher_fixture):
    # role=manager с teacher_id → 400 (refine).
    resp = _client('superadmin').post(
        BASE, {'email': '__y__@example.com', 'role': 'manager', 'teacher_id': teacher_fixture},
        format='json',
    )
    assert resp.status_code == 400


def test_create_teacher_ok(teacher_fixture, cleanup_email):
    cleanup_email.append('__acc_teacher_ok__@example.com')
    resp = _client('superadmin').post(
        BASE,
        {'email': '__acc_teacher_ok__@example.com', 'role': 'teacher', 'teacher_id': teacher_fixture},
        format='json',
    )
    assert resp.status_code == 201
    assert resp.json()['teacher_id'] == teacher_fixture


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------

def test_patch_no_secrets(account_factory):
    acc_id = account_factory(email='__acc_patch__@example.com', role='manager')
    resp = _client('superadmin').patch(f'{BASE}/{acc_id}', {'active': False}, format='json')
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_secrets(body)
    assert body['is_active'] is False


def test_patch_404():
    resp = _client('superadmin').patch(f'{BASE}/999999999', {'active': False}, format='json')
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# reset-password / reset-2fa / delete
# ---------------------------------------------------------------------------

def test_reset_password(account_factory):
    # Создаём реального actor-аккаунта — FK аудита не сломается.
    actor_id = account_factory(email='__actor_rp__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_rp__@example.com')
    resp = _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/reset-password')
    assert resp.status_code == 200
    body = resp.json()
    # Новый контракт: invite_url + expires_at вместо password
    assert set(body.keys()) == {'invite_url', 'expires_at'}
    assert body['invite_url'].startswith('/login/set-password?token=')
    assert body['expires_at']
    # audit-событие password_reset записано
    with connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM security_audit_log WHERE event='password_reset' AND target_id=%s",
            [acc_id],
        )
        assert cur.fetchone()[0] == 1


def test_reset_password_invalidates_sessions(account_factory):
    """Сброс пароля админом немедленно инвалидирует активные сессии (bump token_version)."""
    actor_id = account_factory(email='__actor_rpb__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_rpb__@example.com')
    with connection.cursor() as cur:
        cur.execute('SELECT token_version FROM accounts WHERE id=%s', [acc_id])
        before = cur.fetchone()[0]
    resp = _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/reset-password')
    assert resp.status_code == 200
    with connection.cursor() as cur:
        cur.execute('SELECT token_version FROM accounts WHERE id=%s', [acc_id])
        after = cur.fetchone()[0]
    assert after == before + 1, 'reset-password должен инкрементить token_version'


def test_reset_password_clears_old_password(account_factory):
    """Сброс пароля аннулирует старый пароль: password → NULL/пустой.

    Безопасность: иначе пользователь продолжал бы входить по СТАРОМУ паролю, не
    пользуясь invite-ссылкой. verify_password(пустой хэш) → False, поэтому очистка
    хэша делает вход возможным только после установки нового пароля по invite.
    """
    actor_id = account_factory(email='__actor_rpc__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_rpc__@example.com')
    with connection.cursor() as cur:
        cur.execute('SELECT password FROM accounts WHERE id=%s', [acc_id])
        assert cur.fetchone()[0] is not None  # до сброса пароль установлен
    assert _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/reset-password').status_code == 200
    with connection.cursor() as cur:
        cur.execute('SELECT password FROM accounts WHERE id=%s', [acc_id])
        pw_val = cur.fetchone()[0]
    # После сброса пароль должен быть либо NULL, либо unusable hash (!)
    assert pw_val is None or pw_val.startswith('!'), \
        'после сброса password должен быть NULL или unusable hash'


def test_reset_password_404():
    assert _client('superadmin').post(f'{BASE}/999999999/reset-password').status_code == 404


def test_reset_2fa(account_factory):
    acc_id = account_factory(email='__acc_r2fa__@example.com', twofa=True)
    resp = _client('superadmin').post(f'{BASE}/{acc_id}/reset-2fa')
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}
    with connection.cursor() as cur:
        cur.execute('SELECT twofa_enabled FROM accounts WHERE id=%s', [acc_id])
        assert cur.fetchone()[0] is False


def test_reset_2fa_404():
    assert _client('superadmin').post(f'{BASE}/999999999/reset-2fa').status_code == 404


def test_delete_hard(account_factory):
    acc_id = account_factory(email='__acc_harddel__@example.com')
    resp = _client('superadmin').delete(f'{BASE}/{acc_id}')
    assert resp.status_code == 204
    with connection.cursor() as cur:
        cur.execute('SELECT 1 FROM accounts WHERE id=%s', [acc_id])
        assert cur.fetchone() is None  # hard-delete, строка физически удалена


def test_delete_404():
    assert _client('superadmin').delete(f'{BASE}/999999999').status_code == 404


# ---------------------------------------------------------------------------
# POST /:id/invite — перевыпуск invite-ссылки (Task 4.2)
# ---------------------------------------------------------------------------

def test_invite_regenerate(account_factory):
    """POST /:id/invite возвращает {invite_url, expires_at}."""
    actor_id = account_factory(email='__actor_inv__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_inv__@example.com')
    resp = _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/invite')
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {'invite_url', 'expires_at'}
    assert body['invite_url'].startswith('/login/set-password?token=')
    assert body['expires_at']


def test_invite_regenerate_404():
    assert _client('superadmin').post(f'{BASE}/999999999/invite').status_code == 404


def test_invite_regenerate_revokes_old(account_factory):
    """Перевыпуск отзывает старый инвайт и создаёт новый активный."""
    actor_id = account_factory(email='__actor_inv2__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_inv2__@example.com')

    # Первый инвайт
    resp1 = _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/invite')
    token1_url = resp1.json()['invite_url']

    # Второй инвайт — должен отозвать первый
    resp2 = _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/invite')
    token2_url = resp2.json()['invite_url']

    assert token1_url != token2_url

    # В БД только один активный инвайт
    with connection.cursor() as cur:
        cur.execute(
            'SELECT COUNT(*) FROM account_invites '
            'WHERE account_id=%s AND used_at IS NULL AND revoked_at IS NULL',
            [acc_id],
        )
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# POST /:id/invite/revoke — отзыв инвайта (Task 4.2)
# ---------------------------------------------------------------------------

def test_invite_revoke(account_factory):
    """POST /:id/invite/revoke → {ok: True} и инвайт отозван."""
    actor_id = account_factory(email='__actor_rev__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_rev__@example.com')

    # Выпускаем инвайт
    _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/invite')

    # Отзываем
    resp = _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/invite/revoke')
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}

    # В БД нет активных инвайтов
    with connection.cursor() as cur:
        cur.execute(
            'SELECT COUNT(*) FROM account_invites '
            'WHERE account_id=%s AND used_at IS NULL AND revoked_at IS NULL',
            [acc_id],
        )
        assert cur.fetchone()[0] == 0

    # audit-событие invite_revoked записано
    with connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM security_audit_log WHERE event='invite_revoked' AND target_id=%s",
            [acc_id],
        )
        assert cur.fetchone()[0] == 1


def test_invite_revoke_404():
    assert _client('superadmin').post(f'{BASE}/999999999/invite/revoke').status_code == 404


# ---------------------------------------------------------------------------
# list_accounts: has_active_invite + status (Task 4.2)
# ---------------------------------------------------------------------------

def test_list_status_field(account_factory):
    """list_accounts возвращает поля has_active_invite и status в каждой строке."""
    actor_id = account_factory(email='__actor_lst__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_lst_status__@example.com')

    resp = _client('superadmin', account_id=actor_id).get(BASE, {'filter[email]': '__acc_lst_status__'})
    assert resp.status_code == 200
    rows = resp.json()['rows']
    assert len(rows) == 1
    row = rows[0]
    assert 'status' in row
    assert 'has_active_invite' in row
    # Учётка только создана: нет входа, нет инвайта → 'expired'
    assert row['status'] == 'expired'
    assert row['has_active_invite'] is False


def test_list_status_invited(account_factory):
    """Учётка с активным инвайтом → status='invited'."""
    actor_id = account_factory(email='__actor_lst2__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_lst_invited__@example.com')

    # Выпускаем инвайт
    _client('superadmin', account_id=actor_id).post(f'{BASE}/{acc_id}/invite')

    resp = _client('superadmin', account_id=actor_id).get(BASE, {'filter[email]': '__acc_lst_invited__'})
    assert resp.status_code == 200
    rows = resp.json()['rows']
    assert len(rows) == 1
    assert rows[0]['status'] == 'invited'
    assert rows[0]['has_active_invite'] is True


def test_list_status_disabled(account_factory):
    """Неактивная учётка → status='disabled' независимо от инвайта."""
    actor_id = account_factory(email='__actor_lst3__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_lst_disabled__@example.com')

    # Деактивируем
    _client('superadmin', account_id=actor_id).patch(f'{BASE}/{acc_id}', {'active': False}, format='json')

    resp = _client('superadmin', account_id=actor_id).get(BASE, {'filter[email]': '__acc_lst_disabled__'})
    assert resp.status_code == 200
    rows = resp.json()['rows']
    assert len(rows) == 1
    assert rows[0]['status'] == 'disabled'


# ---------------------------------------------------------------------------
# POST /:id/set-active — обратимое отключение/включение (Task 10)
# ---------------------------------------------------------------------------

def test_set_active_disable_and_enable(account_factory):
    actor_id = account_factory(email='__actor_setact__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_setact__@example.com')

    resp = _client('superadmin', account_id=actor_id).post(
        f'{BASE}/{acc_id}/set-active', {'active': False}, format='json'
    )
    assert resp.status_code == 200
    assert resp.json() == {'ok': True, 'active': False}
    with connection.cursor() as cur:
        cur.execute('SELECT is_active FROM accounts WHERE id=%s', [acc_id])
        assert cur.fetchone()[0] is False

    resp = _client('superadmin', account_id=actor_id).post(
        f'{BASE}/{acc_id}/set-active', {'active': True}, format='json'
    )
    assert resp.status_code == 200
    assert resp.json() == {'ok': True, 'active': True}
    with connection.cursor() as cur:
        cur.execute('SELECT is_active FROM accounts WHERE id=%s', [acc_id])
        assert cur.fetchone()[0] is True


def test_set_active_disable_bumps_token_version(account_factory):
    """Отключение немедленно инвалидирует сессии (bump token_version)."""
    actor_id = account_factory(email='__actor_setact2__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_setact2__@example.com')
    with connection.cursor() as cur:
        cur.execute('SELECT token_version FROM accounts WHERE id=%s', [acc_id])
        before = cur.fetchone()[0]

    resp = _client('superadmin', account_id=actor_id).post(
        f'{BASE}/{acc_id}/set-active', {'active': False}, format='json'
    )
    assert resp.status_code == 200
    with connection.cursor() as cur:
        cur.execute('SELECT token_version FROM accounts WHERE id=%s', [acc_id])
        after = cur.fetchone()[0]
    assert after == before + 1, 'set-active(active=False) должен инкрементить token_version'

    with connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM security_audit_log WHERE event='account_disabled' AND target_id=%s",
            [acc_id],
        )
        assert cur.fetchone()[0] == 1


def test_set_active_enable_no_bump(account_factory):
    """Включение НЕ инвалидирует сессии (нет причины разлогинивать при возврате доступа)."""
    actor_id = account_factory(email='__actor_setact3__@example.com', role='superadmin')
    acc_id = account_factory(email='__acc_setact3__@example.com', is_active=False)
    with connection.cursor() as cur:
        cur.execute('SELECT token_version FROM accounts WHERE id=%s', [acc_id])
        before = cur.fetchone()[0]

    resp = _client('superadmin', account_id=actor_id).post(
        f'{BASE}/{acc_id}/set-active', {'active': True}, format='json'
    )
    assert resp.status_code == 200
    with connection.cursor() as cur:
        cur.execute('SELECT token_version FROM accounts WHERE id=%s', [acc_id])
        after = cur.fetchone()[0]
    assert after == before, 'set-active(active=True) НЕ должен менять token_version'

    with connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM security_audit_log WHERE event='account_enabled' AND target_id=%s",
            [acc_id],
        )
        assert cur.fetchone()[0] == 1


def test_set_active_404():
    resp = _client('superadmin').post(f'{BASE}/999999999/set-active', {'active': False}, format='json')
    assert resp.status_code == 404


def test_set_active_forbidden_for_manager(manager_client):
    resp = manager_client.post(f'{BASE}/1/set-active', {'active': False}, format='json')
    assert resp.status_code == 403
