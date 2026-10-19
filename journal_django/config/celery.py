"""
Celery-приложение journal_django (Фаза 3, спека 2026-07-11).

Введено «понемногу»: единственная задача пока — прогрев кэша снимка «Реестра
куратора» (apps/dashboard/tasks.refresh_registry_snapshot). Broker и
result-backend — Redis (settings.CELERY_BROKER_URL).

Локально Celery можно НЕ запускать (и даже не устанавливать): config/__init__.py
импортирует этот модуль под try/except, а registry_service считает снимок
синхронно при холодном кэше. Запуск в проде — deploy/systemd/celery-*.service.
"""
from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

app = Celery('journal')
# Конфиг из Django settings, префикс CELERY_ (CELERY_BROKER_URL и т.д.).
app.config_from_object('django.conf:settings', namespace='CELERY')
# Автоподхват apps.<app>.tasks у зарегистрированных приложений.
app.autodiscover_tasks()
