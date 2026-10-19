# journal_django/apps/sync/tests/test_views.py
import pytest


@pytest.mark.django_db
def test_run_requires_superadmin(admin_client):
    """admin (не superadmin) не должен пройти — см. apps.core.permissions.IsSuperAdmin."""
    resp = admin_client.post('/api/admin/sync/rebuild-counters/run', {'dry_run': True}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_run_rejects_anonymous(anon_client):
    resp = anon_client.post('/api/admin/sync/rebuild-counters/run', {'dry_run': True}, format='json')
    assert resp.status_code == 401


@pytest.mark.django_db
def test_run_unknown_action_404(superadmin_client):
    resp = superadmin_client.post('/api/admin/sync/does-not-exist/run', {'dry_run': True}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize('action', [
    'teachers', 'groups', 'students', 'lessons', 'payments', 'payroll', 'run-all',
])
def test_sheets_loader_actions_removed(superadmin_client, action):
    """Загрузчики из Google-таблиц KOTOKOD удалены — их URL отвечают 404, а не
    ставят задачу в очередь."""
    resp = superadmin_client.post(f'/api/admin/sync/{action}/run', {'dry_run': True}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_run_and_status_happy_path(superadmin_client, monkeypatch):
    """CELERY_TASK_ALWAYS_EAGER=True в тестах (нет REDIS_URL) — .delay() выполняется
    синхронно; CELERY_TASK_STORE_EAGER_RESULT=True (Task 1) кладёт результат в backend,
    поэтому последующий GET .../status/<task_id> его находит."""
    monkeypatch.setattr(
        'apps.sync.backfills.rebuild_counters.run',
        lambda dry_run=False: {'entity': 'counters-rebuild', 'updated': 3, 'dry_run': dry_run},
    )

    run_resp = superadmin_client.post('/api/admin/sync/rebuild-counters/run', {'dry_run': True}, format='json')
    assert run_resp.status_code == 202
    task_id = run_resp.data['task_id']
    assert task_id

    status_resp = superadmin_client.get(f'/api/admin/sync/status/{task_id}')
    assert status_resp.status_code == 200
    assert status_resp.data['state'] == 'SUCCESS'
    assert status_resp.data['result'] == {'entity': 'counters-rebuild', 'updated': 3, 'dry_run': True}
    assert status_resp.data['error'] is None


@pytest.mark.django_db
def test_run_coerces_string_dry_run_false(superadmin_client, monkeypatch):
    """dry_run приходит как JSON-строка 'false' (не bool) — raw Python bool('false') дал бы
    True (непустая строка truthy). DRF BooleanField должен корректно распознать это как False."""
    captured = {}

    def fake_run(dry_run=False):
        captured['dry_run'] = dry_run
        return {'entity': 'counters-rebuild', 'updated': 0, 'dry_run': dry_run}

    monkeypatch.setattr('apps.sync.backfills.rebuild_counters.run', fake_run)

    run_resp = superadmin_client.post('/api/admin/sync/rebuild-counters/run', {'dry_run': 'false'}, format='json')
    assert run_resp.status_code == 202
    assert captured['dry_run'] is False


@pytest.mark.django_db
def test_run_rebuild_planned_lessons_action_reachable(superadmin_client, monkeypatch):
    """Проверка, что новое destructive-действие подключено end-to-end через URL
    (не только на уровне задачи) — см. apps.sync.views.ACTIONS."""
    monkeypatch.setattr(
        'apps.sync.backfills.rebuild_planned_lessons.run',
        lambda dry_run=False: {'entity': 'planned-lessons-rebuild', 'dry_run': dry_run},
    )

    run_resp = superadmin_client.post(
        '/api/admin/sync/rebuild-planned-lessons/run', {'dry_run': True}, format='json',
    )
    assert run_resp.status_code == 202
    assert run_resp.data['task_id']


@pytest.mark.django_db
def test_run_rebuild_renewals_action_reachable(superadmin_client, monkeypatch):
    """rebuild-renewals подключён end-to-end через URL (destructive-действие)."""
    monkeypatch.setattr(
        'apps.sync.backfills.rebuild_renewals.run',
        lambda dry_run=False: {'entity': 'renewals-rebuild', 'dry_run': dry_run},
    )
    run_resp = superadmin_client.post(
        '/api/admin/sync/rebuild-renewals/run', {'dry_run': True}, format='json',
    )
    assert run_resp.status_code == 202
    assert run_resp.data['task_id']


@pytest.mark.django_db
def test_status_reports_failure(superadmin_client, monkeypatch):
    def boom(dry_run=False):
        raise RuntimeError('пересчёт упал')

    monkeypatch.setattr('apps.sync.backfills.rebuild_counters.run', boom)

    run_resp = superadmin_client.post('/api/admin/sync/rebuild-counters/run', {'dry_run': False}, format='json')
    task_id = run_resp.data['task_id']

    status_resp = superadmin_client.get(f'/api/admin/sync/status/{task_id}')
    assert status_resp.data['state'] == 'FAILURE'
    assert 'пересчёт упал' in status_resp.data['error']
