"""AppConfig приложения changelog (журнал изменений)."""
from django.apps import AppConfig


class ChangelogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.changelog'

    def ready(self) -> None:
        _make_pghistory_json_fields_tolerant()


def _make_pghistory_json_fields_tolerant() -> None:
    """
    Проект регистрирует psycopg2 jsonb-typecaster на каждом соединении
    (apps/core/apps.py): jsonb приходит в Python уже dict/list. Штатный
    JSONField (и pghistory.utils.JSONField) в from_db_value делает json.loads
    повторно и падает — для своих моделей проект решает это TolerantJSONField
    (apps/core/db_fields.py).

    Моделям pghistory (Context.metadata, Events.pgh_data/pgh_diff/pgh_context)
    и клонированным JSON-полям авто-генерируемых *Event-моделей класс поля не
    задать декларативно — поэтому здесь, один раз при старте, подменяем класс
    каждого такого поля на толерантный сабкласс (то же поведение, что
    TolerantJSONField, но с сохранением реального базового класса поля).
    """
    from django.apps import apps as django_apps
    from django.db import models as dj_models

    import pghistory.models

    tolerant_cache: dict[type, type] = {}

    def tolerant_subclass(base_cls: type) -> type:
        cached = tolerant_cache.get(base_cls)
        if cached is not None:
            return cached

        def from_db_value(self, value, expression, connection):
            if value is None or isinstance(value, (dict, list, int, float, bool)):
                return value
            return base_cls.from_db_value(self, value, expression, connection)

        def deconstruct(self):
            # Подмена класса поля — ЧИСТО рантайм (для jsonb-толерантности) и НЕ
            # должна быть видна автодетектору миграций: иначе Django считает поле
            # изменённым и пишет мусорную AlterField-миграцию pghistory прямо в
            # site-packages с неимпортируемым путём (apps.changelog.apps.Tolerant…).
            # Деконструируем КАК БАЗОВОЕ поле: временно возвращаем __class__ к
            # base_cls, чтобы Field.deconstruct дал нормализованный путь базы.
            real_cls = self.__class__
            self.__class__ = base_cls
            try:
                return base_cls.deconstruct(self)
            finally:
                self.__class__ = real_cls

        subclass = type(
            'Tolerant' + base_cls.__name__, (base_cls,),
            {'from_db_value': from_db_value, 'deconstruct': deconstruct, '_chg_tolerant': True},
        )
        tolerant_cache[base_cls] = subclass
        return subclass

    targets = [
        model for model in django_apps.get_models()
        if model._meta.app_label == 'pghistory'
        or issubclass(model, pghistory.models.Event)
    ]
    for model in targets:
        for field in model._meta.local_fields:
            if isinstance(field, dj_models.JSONField) and not getattr(field, '_chg_tolerant', False):
                field.__class__ = tolerant_subclass(type(field))
