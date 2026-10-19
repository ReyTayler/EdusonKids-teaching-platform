"""AppConfig раздела «База знаний»."""
from __future__ import annotations

from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'
    name = 'apps.knowledge'
    label = 'knowledge'
    verbose_name = 'База знаний'
