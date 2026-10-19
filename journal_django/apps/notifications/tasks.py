"""
Celery-задачи уведомлений.

Логика — в dispatcher.py и digests.py (тестируются без Celery); задачи только
делегируют. Модуль подхватывается автодискавером Celery, когда воркер запущен.
"""
from __future__ import annotations

from celery import shared_task

from apps.notifications import dispatcher, digests


@shared_task(name='apps.notifications.tasks.dispatch_outbox')
def dispatch_outbox() -> int:
    """Разгрести очередь. Возвращает число отправленных сообщений."""
    return dispatcher.dispatch()


@shared_task(name='apps.notifications.tasks.send_morning_digest')
def send_morning_digest() -> int:
    """Утренний список занятий (8:00 МСК)."""
    return digests.send_morning_digest()


@shared_task(name='apps.notifications.tasks.send_fill_digest')
def send_fill_digest() -> int:
    """Вечерний список незаполненных отчётов (21:00 МСК)."""
    return digests.send_fill_digest()
