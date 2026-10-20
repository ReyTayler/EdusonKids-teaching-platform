# journal_django/apps/sync/tests/test_tasks.py
import pytest

from apps.sync import tasks


@pytest.mark.django_db
def test_check_plan_health_task_delegates(monkeypatch):
    monkeypatch.setattr(
        'apps.sync.backfills.check_plan_health.run',
        lambda dry_run=False: {'entity': 'plan-health', 'dry_run': dry_run},
    )
    result = tasks.check_plan_health_task.run(dry_run=True)
    assert result == {'entity': 'plan-health', 'dry_run': True}


def test_only_plan_health_task_left():
    """Пересборки по данным БД удалены (2026-10-20) — в модуле задач их нет."""
    names = {n for n in dir(tasks) if n.endswith('_task') and n != 'shared_task'}
    assert names == {'check_plan_health_task'}
