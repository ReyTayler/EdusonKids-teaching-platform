"""AppConfig раздела «Задачи»."""
from django.apps import AppConfig


class TaskboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.taskboard'
    label = 'taskboard'
