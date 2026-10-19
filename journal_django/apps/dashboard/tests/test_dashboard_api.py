"""
E2E тесты для /api/admin/dashboard (DRF APIClient, реальная БД managed=False).

Дашборд агрегирует всю базу — точные суммы сверяет e2e-diff с Express (golden).
Здесь: auth, валидация (invalid_date), форма ответа и ТИПЫ (числа, не строки).
"""
from __future__ import annotations

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Account

pytestmark = pytest.mark.django_db

BASE = '/api/admin/dashboard'

_ROLE_EMAILS = {
    'admin': '__dash_admin__@example.com',
    'manager': '__dash_manager__@example.com',
    'teacher': '__dash_teacher__@example.com',
}
_CREATED_IDS: list[int] = []


@pytest.fixture(scope='session')
def django_db_setup():
    pass


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
                cur.execute("INSERT INTO teachers (name) VALUES ('__dash_teacher__') RETURNING id")
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


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_requires_auth():
    assert _client(None).get(BASE).status_code == 401


def test_teacher_forbidden():
    assert _client('teacher').get(BASE).status_code == 403


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('role', ['manager', 'admin'])
def test_dashboard_shape_and_types(role):
    resp = _client(role).get(BASE)
    assert resp.status_code == 200
    body = resp.json()
    # Карточка «Долги» удалена из дашборда (2026-08-02) вместе с расчётом:
    # полей debts/debts_total в ответе больше нет.
    assert set(body.keys()) == {
        'month', 'from', 'to', 'revenue_month', 'worked_off_month',
        'carryover_month', 'deferred_total', 'recognized_daily', 'recognized_monthly',
    }
    # Денежные значения — JSON-числа, не строки (как Express Number()).
    for k in ('revenue_month', 'worked_off_month', 'carryover_month', 'deferred_total'):
        assert isinstance(body[k], (int, float)), f'{k} must be number, got {type(body[k])}'


def test_dashboard_invalid_date():
    resp = _client('manager').get(BASE, {'from': '2026-13-99'})
    assert resp.status_code == 400
    assert resp.json() == {'error': 'invalid_date'}


def test_dashboard_invalid_date_impossible_day():
    resp = _client('manager').get(BASE, {'to': '2026-02-30'})
    assert resp.status_code == 400


def test_dashboard_valid_range_echoes_params():
    resp = _client('manager').get(BASE, {'from': '2026-01-01', 'to': '2026-12-31'})
    assert resp.status_code == 200
    body = resp.json()
    assert body['from'] == '2026-01-01'
    assert body['to'] == '2026-12-31'


def test_recognized_series_cover_period():
    """Ряды «Recognized revenue»: день на каждую дату периода + свёртка по месяцам."""
    resp = _client('manager').get(BASE, {'from': '2026-01-01', 'to': '2026-02-10'})
    body = resp.json()
    daily, monthly = body['recognized_daily'], body['recognized_monthly']

    assert len(daily) == 41                       # 31 января + 10 февраля
    assert daily[0]['date'] == '2026-01-01'
    assert daily[-1]['date'] == '2026-02-10'
    assert all(isinstance(d['recognized'], (int, float)) for d in daily)

    assert [m['month'] for m in monthly] == ['2026-01', '2026-02']
    # Свёртка месяца = сумма его дней (сравниваем в копейках, без float-дрейфа).
    for m in monthly:
        days = sum(round(d['recognized'] * 100) for d in daily if d['date'][:7] == m['month'])
        assert days == round(m['recognized'] * 100)


def test_recognized_daily_sums_to_worked_off():
    """Сумма дневного ряда = KPI «Отработано за период» (тот же FIFO-проход)."""
    body = _client('manager').get(BASE, {'from': '2026-01-01', 'to': '2026-03-31'}).json()
    total = sum(round(d['recognized'] * 100) for d in body['recognized_daily'])
    assert total == round(body['worked_off_month'] * 100)


def test_dashboard_default_period_is_last_three_months():
    from apps.dashboard.services import default_period
    body = _client('manager').get(BASE).json()
    assert (body['from'], body['to']) == default_period()
