"""AppConfig для раздела «Продления»."""
from django.apps import AppConfig


class RenewalsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.renewals'
    label = 'renewals'

    def ready(self) -> None:
        # Подключаем сигналы при старте приложения (Payment/Attendance).
        from apps.renewals import signals  # noqa: F401
