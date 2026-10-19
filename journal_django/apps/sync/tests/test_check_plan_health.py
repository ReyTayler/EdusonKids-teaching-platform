# journal_django/apps/sync/tests/test_check_plan_health.py
"""Проверка планов в «Синхро»: обёртка + RBAC. Read-only действие."""
import pytest

from apps.sync.backfills import check_plan_health

pytestmark = pytest.mark.django_db


def test_run_returns_plan_health_report():
    result = check_plan_health.run()
    assert result['entity'] == 'plan-health'
    assert 'checked' in result
    assert isinstance(result['groups'], list)


def test_run_accepts_dry_run_flag():
    """dry_run для read-only действия бессмыслен, но сигнатура общая с
    остальными backfill-модулями (apps/sync/views.py передаёт его всегда)."""
    assert check_plan_health.run(dry_run=True)['entity'] == 'plan-health'


def test_endpoint_requires_superadmin(admin_client):
    resp = admin_client.post('/api/admin/sync/check-plan-health/run', {}, format='json')
    assert resp.status_code == 403


def test_endpoint_runs_for_superadmin(superadmin_client):
    resp = superadmin_client.post('/api/admin/sync/check-plan-health/run', {}, format='json')
    assert resp.status_code == 202
