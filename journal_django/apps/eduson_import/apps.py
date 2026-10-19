from django.apps import AppConfig


class EdusonImportConfig(AppConfig):
    # Моделей нет: приложение — разовый перенос данных старой системы Eduson
    # (SQLite) в модели журнала. Зарегистрировано ради management-команды
    # import_eduson и общего тест-раннера.
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.eduson_import'
    label = 'eduson_import'
