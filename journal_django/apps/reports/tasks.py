"""Celery-задачи apps.reports — генерация отчётов в фоне (очередь 'default').

Готовый xlsx НЕ сохраняется на платформе: задача возвращает его прямо в
результате (base64), а перенос до клиента делает celery result backend
(Redis в проде / in-memory + STORE_EAGER_RESULT локально). Вьюха скачивания
читает результат по task_id и стримит файл. Вся сборка — в services/builders.
"""
from __future__ import annotations

import base64

from celery import shared_task

from apps.reports import services


@shared_task(name='apps.reports.tasks.generate_report_task', time_limit=300)
def generate_report_task(report_type: str, params: dict) -> dict:
    """Сгенерировать отчёт. Результат: {filename, row_count, content_b64}."""
    content, row_count, filename = services.build_report(report_type, params)
    return {
        'filename': filename,
        'row_count': row_count,
        'content_b64': base64.b64encode(content).decode('ascii'),
    }
