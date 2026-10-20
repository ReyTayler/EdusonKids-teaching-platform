# journal_django/apps/sync/tests/test_views.py
import pytest

ACTION_URL = '/api/admin/sync/check-plan-health/run'


@pytest.mark.django_db
def test_run_requires_superadmin(admin_client):
    """admin (не superadmin) не должен пройти — см. apps.core.permissions.IsSuperAdmin."""
    resp = admin_client.post(ACTION_URL, {'dry_run': True}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_run_rejects_anonymous(anon_client):
    resp = anon_client.post(ACTION_URL, {'dry_run': True}, format='json')
    assert resp.status_code == 401


@pytest.mark.django_db
def test_run_unknown_action_404(superadmin_client):
    resp = superadmin_client.post('/api/admin/sync/does-not-exist/run', {'dry_run': True}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize('action', [
    # загрузчики из Google-таблиц KOTOKOD
    'teachers', 'groups', 'students', 'lessons', 'payments', 'payroll', 'run-all',
    # пересборки по данным БД (2026-10-20): несовместимы с данными Eduson
    'rebuild-payroll', 'rebuild-counters', 'rebuild-planned-lessons', 'rebuild-absence-resolutions',
    'rebuild-renewals', 'rebuild-renewal-dates',
])
def test_removed_actions_404(superadmin_client, action):
    """Удалённые действия отвечают 404, а не ставят задачу в очередь."""
    resp = superadmin_client.post(f'/api/admin/sync/{action}/run', {'dry_run': True}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_run_and_status_happy_path(superadmin_client, monkeypatch):
    """CELERY_TASK_ALWAYS_EAGER=True в тестах (нет REDIS_URL) — .delay() выполняется
    синхронно; CELERY_TASK_STORE_EAGER_RESULT=True кладёт результат в backend,
    поэтому последующий GET .../status/<task_id> его находит."""
    monkeypatch.setattr(
        'apps.sync.backfills.check_plan_health.run',
        lambda dry_run=False: {'entity': 'plan-health', 'checked': 3, 'dry_run': dry_run},
    )

    run_resp = superadmin_client.post(ACTION_URL, {'dry_run': True}, format='json')
    assert run_resp.status_code == 202
    task_id = run_resp.data['task_id']
    assert task_id

    status_resp = superadmin_client.get(f'/api/admin/sync/status/{task_id}')
    assert status_resp.status_code == 200
    assert status_resp.data['state'] == 'SUCCESS'
    assert status_resp.data['result'] == {'entity': 'plan-health', 'checked': 3, 'dry_run': True}
    assert status_resp.data['error'] is None


@pytest.mark.django_db
def test_run_coerces_string_dry_run_false(superadmin_client, monkeypatch):
    """dry_run приходит как JSON-строка 'false' (не bool) — raw Python bool('false') дал бы
    True (непустая строка truthy). DRF BooleanField должен корректно распознать это как False."""
    captured = {}

    def fake_run(dry_run=False):
        captured['dry_run'] = dry_run
        return {'entity': 'plan-health', 'dry_run': dry_run}

    monkeypatch.setattr('apps.sync.backfills.check_plan_health.run', fake_run)

    run_resp = superadmin_client.post(ACTION_URL, {'dry_run': 'false'}, format='json')
    assert run_resp.status_code == 202
    assert captured['dry_run'] is False


@pytest.mark.django_db
def test_status_reports_failure(superadmin_client, monkeypatch):
    def boom(dry_run=False):
        raise RuntimeError('проверка упала')

    monkeypatch.setattr('apps.sync.backfills.check_plan_health.run', boom)

    run_resp = superadmin_client.post(ACTION_URL, {'dry_run': False}, format='json')
    task_id = run_resp.data['task_id']

    status_resp = superadmin_client.get(f'/api/admin/sync/status/{task_id}')
    assert status_resp.data['state'] == 'FAILURE'
    assert 'проверка упала' in status_resp.data['error']
