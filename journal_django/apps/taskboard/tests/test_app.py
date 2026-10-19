"""Приложение зарегистрировано под ожидаемым label."""
from django.apps import apps


def test_taskboard_app_registered():
    config = apps.get_app_config('taskboard')
    assert config.name == 'apps.taskboard'
