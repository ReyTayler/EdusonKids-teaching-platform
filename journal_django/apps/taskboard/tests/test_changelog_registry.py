"""Модели taskboard зарегистрированы в журнале изменений."""
from apps.changelog.registry import TRACKED


def test_taskboard_models_registered():
    expected = {
        'taskboard.TaskBoard': ('task_board', True),
        'taskboard.TaskStage': ('task_stage', True),
        'taskboard.Task': ('task', True),
        'taskboard.TaskActivity': ('task_activity', False),
    }
    for key, (entity, revertable) in expected.items():
        assert key in TRACKED, f'{key} не зарегистрирована'
        assert TRACKED[key].entity == entity
        assert TRACKED[key].revertable is revertable


def test_task_label_rules_present():
    from apps.changelog.labels import RULES

    ops = {op for _, _, op in RULES}
    assert 'task.create' in ops
    assert 'task.move' in ops
    assert 'task.complete' in ops
