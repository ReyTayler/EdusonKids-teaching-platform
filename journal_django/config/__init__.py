"""
Guarded Celery-app import.

Подхватывает config/celery.py, чтобы `celery -A config` находил приложение, а
shared_task-декораторы регистрировались. Обёрнут в try/except ImportError:
локально/в тестах, где пакет celery не установлен, Django стартует БЕЗ Celery
(«Реестр куратора» считается синхронно — см. apps/dashboard/registry_service).
"""
try:
    from .celery import app as celery_app

    __all__ = ('celery_app',)
except ImportError:
    # celery не установлен (напр. локальная разработка на Windows) — не мешаем старту.
    __all__ = ()
