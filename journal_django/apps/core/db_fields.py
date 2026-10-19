"""
Custom model fields for journal_django (ORM-миграция, раздел 09).

TolerantJSONField — JSONField, терпимый к уже-декодированным значениям.

Проект регистрирует jsonb-typecaster psycopg2 на каждом соединении
(apps/core/apps.py), чтобы сырые репозитории получали dict/list и оставались
байт-в-байт совместимы с Express. Побочный эффект: ORM тоже получает уже
разобранные значения, а штатный JSONField.from_db_value повторно вызывает
json.loads и падает ("the JSON object must be str ... not dict"). Это поле
пропускает dict/list/числа/bool/None без повторного разбора.

deconstruct() рапортует поле как штатный django.db.models.JSONField — поэтому
makemigrations --check НЕ генерирует лишний AlterField (схема managed=True
не «плывёт»; существующие миграции с models.JSONField остаются валидными).
"""
from __future__ import annotations

from django.db import models


class TolerantJSONField(models.JSONField):
    """JSONField, пропускающий уже-декодированные jsonb-значения без json.loads."""

    def from_db_value(self, value, expression, connection):
        # psycopg2 + register_default_jsonb уже отдал готовый Python-объект.
        if value is None or isinstance(value, (dict, list, int, float, bool)):
            return value
        return super().from_db_value(value, expression, connection)

    def deconstruct(self):
        name, _path, args, kwargs = super().deconstruct()
        # Рапортуем как штатный JSONField → нет diff в makemigrations --check.
        return name, 'django.db.models.JSONField', args, kwargs
