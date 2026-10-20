from django.apps import AppConfig


class EdusonImportConfig(AppConfig):
    # Перенос данных старой системы Eduson (SQLite) в модели журнала: полный
    # (import_eduson) и мягкий (sync_eduson). Своя модель одна — таблица
    # соответствий EdusonLink.
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.eduson_import'
    label = 'eduson_import'
