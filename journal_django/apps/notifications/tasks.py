"""
Celery-задачи уведомлений.

Логика — в dispatcher.py и digests.py (тестируются без Celery); задачи только
делегируют. Модуль подхватывается автодискавером Celery, когда воркер запущен.
"""
from __future__ import annotations

from celery import shared_task

from apps.notifications import dispatcher, digests, reminders


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


# --- Email-напоминания (спека этапа 3, раздел D) ---------------------------

@shared_task(name='apps.notifications.tasks.send_teacher_schedule_email')
def send_teacher_schedule_email() -> int:
    """Письмо «Расписание на сегодня» (08:00 МСК)."""
    return reminders.send_teacher_schedule()


@shared_task(name='apps.notifications.tasks.send_unrecorded_reminders')
def send_unrecorded_reminders() -> int:
    """Письма «Урок не записан» и повторы (каждые 15 минут)."""
    return reminders.send_unrecorded_reminders()


@shared_task(name='apps.notifications.tasks.send_parent_day_before')
def send_parent_day_before() -> int:
    """Родителям «урок завтра» (10:00 МСК)."""
    return reminders.send_parent_day_before()


@shared_task(name='apps.notifications.tasks.send_parent_hour_before')
def send_parent_hour_before() -> int:
    """Родителям «урок через час» (каждые 5 минут)."""
    return reminders.send_parent_hour_before()
