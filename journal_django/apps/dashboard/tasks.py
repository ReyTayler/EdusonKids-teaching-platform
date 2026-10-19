"""
Celery-задачи dashboard (спеки 2026-07-11 и 2026-07-13).

Прогревы кэшей: сводка «Реестра куратора» и финансовая сводка (default-ключ).
Модуль импортируется Celery-автодискавером ТОЛЬКО когда воркер/beat запущены
(прод). Локально, где пакет celery не установлен, модуль не грузится.

Вся логика — в registry_service.refresh_summary / services.refresh_dashboard
(тестируются без Celery); задачи лишь делегируют.
"""
from __future__ import annotations

from celery import shared_task

from apps.dashboard import registry_service, services


@shared_task(name='apps.dashboard.tasks.refresh_registry_summary')
def refresh_registry_summary() -> str:
    """Пересчитать сводку реестра и положить в кэш. Возвращает generated_at."""
    return registry_service.refresh_summary()


@shared_task(name='apps.dashboard.tasks.refresh_finance_dashboard')
def refresh_finance_dashboard() -> str:
    """Пересчитать финансовую сводку (дефолт — последние 3 месяца) и положить в кэш."""
    return services.refresh_dashboard()
