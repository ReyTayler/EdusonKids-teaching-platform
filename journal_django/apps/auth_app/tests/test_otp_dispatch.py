"""
Диспатч OTP-письма (Celery-спека 2026-07-13, фаза A).

services.send_otp_email — точка-шов (её мокают API-тесты входа/2FA). Поведение:
  • celery доступен → письмо уходит фоновой задачей send_otp_email_task.delay —
    SMTP не блокирует запрос /login;
  • пакет celery не установлен (локальный dev) → синхронный fallback через mailer.

Пакет celery локально может отсутствовать, поэтому модуль задач в тестах
подменяется фейком в sys.modules (реальный apps.auth_app.tasks импортируется
только там, где celery установлен — прод-воркер и прод-веб).
"""
from __future__ import annotations

import sys
import types
from unittest import mock

from django.conf import settings

from apps.auth_app import services


def test_dispatch_uses_celery_task_when_available():
    """celery есть → .delay(to, code), прямой SMTP-вызов не делается."""
    fake_tasks = types.ModuleType('apps.auth_app.tasks')
    fake_tasks.send_otp_email_task = mock.Mock()
    with mock.patch.dict(sys.modules, {'apps.auth_app.tasks': fake_tasks}):
        with mock.patch.object(services.mailer, 'send_otp_email') as direct:
            services.send_otp_email('user@example.com', '123456')
    fake_tasks.send_otp_email_task.delay.assert_called_once_with(
        'user@example.com', '123456')
    direct.assert_not_called()


def test_dispatch_falls_back_to_sync_without_celery():
    """celery не установлен (ImportError) → письмо уходит синхронно через mailer."""
    with mock.patch.dict(sys.modules, {'apps.auth_app.tasks': None}):
        with mock.patch.object(services.mailer, 'send_otp_email') as direct:
            services.send_otp_email('user@example.com', '654321')
    direct.assert_called_once_with('user@example.com', '654321')


def test_otp_task_routed_to_interactive_queue():
    """Письмо входа не должно стоять в очереди за тяжёлыми фоновыми задачами."""
    route = settings.CELERY_TASK_ROUTES['apps.auth_app.tasks.send_otp_email_task']
    assert route == {'queue': 'interactive'}


def test_default_queue_name():
    """Все остальные задачи (прогревы, ночные) идут в очередь default."""
    assert settings.CELERY_TASK_DEFAULT_QUEUE == 'default'
