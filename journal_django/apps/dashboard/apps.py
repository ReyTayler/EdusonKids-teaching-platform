from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.dashboard'
    label = 'dashboard'

    def ready(self) -> None:
        # Подписка на сигналы Payment для инвалидации кэша реестра (read-model
        # слушает write-домен, не наоборот). Импорт здесь — стандартный паттерн.
        from apps.dashboard import signals  # noqa: F401
