# journal_django/apps/sync/tasks.py
"""Celery-задачи apps.sync — обёртки над backfills/*.py.

Очередь 'default' (не 'interactive') — не конкурируют с OTP-письмами входа за
приоритет. В Eduson осталась только проверка плана (только чтение): загрузчики
из Google-таблиц KOTOKOD удалены (искали ученика по full_name, а у Eduson тёзки),
пересборки по данным БД — тоже (2026-10-20): они стирали офсеты «пройдено до
платформы», расписание и стадии продлений, перенесённые из старой системы.
Данные старой системы обновляет мягкий импорт (apps.sync.prod_refresh).
"""
from __future__ import annotations

from celery import shared_task

from apps.sync.backfills import check_plan_health


@shared_task(name='apps.sync.tasks.check_plan_health_task', time_limit=120)
def check_plan_health_task(dry_run: bool = False) -> dict:
    return check_plan_health.run(dry_run=dry_run)
