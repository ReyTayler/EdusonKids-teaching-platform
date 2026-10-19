# journal_django/apps/sync/tests/test_tasks.py
import pytest

from apps.sync import tasks


@pytest.mark.django_db
def test_rebuild_counters_task_delegates(monkeypatch):
    monkeypatch.setattr(
        'apps.sync.backfills.rebuild_counters.run',
        lambda dry_run=False: {'entity': 'counters-rebuild', 'dry_run': dry_run},
    )
    result = tasks.rebuild_counters_task.run(dry_run=True)
    assert result == {'entity': 'counters-rebuild', 'dry_run': True}


@pytest.mark.django_db
def test_rebuild_planned_lessons_task_delegates(monkeypatch):
    monkeypatch.setattr(
        'apps.sync.backfills.rebuild_planned_lessons.run',
        lambda dry_run=False: {'entity': 'planned-lessons-rebuild', 'dry_run': dry_run},
    )
    result = tasks.rebuild_planned_lessons_task.run(dry_run=True)
    assert result == {'entity': 'planned-lessons-rebuild', 'dry_run': True}
