"""
E2E тесты для /api/admin/payroll (DRF APIClient, реальная БД managed=False).

Покрытие: auth (401/403/200), список+контракт, summary (lessons_count строкой),
PATCH 200/404, N+1 на списке.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Account
from django.contrib.auth.hashers import make_password

pytestmark = pytest.mark.django_db

BASE_URL = '/api/admin/payroll'

_ROLE_EMAILS = {
    'admin': '__pr_admin__@example.com',
    'manager': '__pr_manager__@example.com',
    'teacher': '__pr_teacher_acc__@example.com',
    'superadmin': '__pr_superadmin__@example.com',
}
_CREATED_IDS: list[int] = []


def _get_or_create_account(role: str) -> 'Account':
    email = _ROLE_EMAILS[role]
    try:
        return Account.objects.get(email=email)
    except Account.DoesNotExist:
        from django.db import connection as _conn
        with _conn.cursor() as cur:
            # role='teacher' требует teacher_id (CHECK accounts_teacher_role_check).
            teacher_id = None
            if role == 'teacher':
                cur.execute("INSERT INTO teachers (name) VALUES ('__payroll_teacher__') RETURNING id")
                teacher_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, is_superuser, first_name, last_name, date_joined, token_version) "
                "VALUES (%s, %s, %s, %s, true, false, false, '', '', NOW(), 0) RETURNING id",
                [email, make_password('testpass123'), role, teacher_id],
            )
            acc_id = cur.fetchone()[0]
            _CREATED_IDS.append(acc_id)
        return Account.objects.get(pk=acc_id)


def _client(role: str | None) -> APIClient:
    c = APIClient()
    if role is not None:
        account = _get_or_create_account(role)
        refresh = RefreshToken.for_user(account)
        refresh['token_version'] = account.token_version
        c.cookies['access'] = str(refresh.access_token)
    return c


def test_list_requires_auth():
    assert _client(None).get(BASE_URL).status_code == 401


def test_list_teacher_forbidden():
    assert _client('teacher').get(BASE_URL).status_code == 403


def test_list_superadmin_only(manager_client, admin_client, superadmin_client):
    """КРИТИЧНО: payroll доступен только superadmin — manager и admin получают 403."""
    for c in (manager_client, admin_client):
        assert c.get(BASE_URL).status_code == 403
    resp = superadmin_client.get(BASE_URL)
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {'rows', 'total', 'page', 'page_size'}


def test_list_filter(payroll_fixture, teacher_id_fixture, group_fixture):
    resp = _client('superadmin').get(
        BASE_URL, {'filter[teacher_id]': teacher_id_fixture, 'filter[group_id]': group_fixture}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 1
    assert body['rows'][0]['payment'] == '650.00'  # numeric → строка


def test_summary_string_count(payroll_fixture, teacher_id_fixture):
    resp = _client('superadmin').get(f'{BASE_URL}/summary', {'teacher_id': teacher_id_fixture})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]['lessons_count'] == '1'          # bigint → строка
    assert rows[0]['sum_payment'] == '650.00'


def test_patch_payroll(payroll_fixture):
    payroll_id, _ = payroll_fixture
    resp = _client('superadmin').patch(
        f'{BASE_URL}/{payroll_id}', {'penalty': 40}, format='json'
    )
    assert resp.status_code == 200
    assert resp.json()['penalty'] == '40.00'


def test_patch_404():
    resp = _client('superadmin').patch(f'{BASE_URL}/999999999', {'penalty': 40}, format='json')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


def test_list_no_n_plus_1(payroll_fixture, teacher_id_fixture):
    client = _client('superadmin')
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(BASE_URL, {'filter[teacher_id]': teacher_id_fixture})
        assert resp.status_code == 200
    # COUNT + rows ≤ 4 запросов (нет N+1 по lessons/teachers/groups)
    assert len(ctx.captured_queries) <= 4
