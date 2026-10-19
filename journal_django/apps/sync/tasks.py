# journal_django/apps/sync/tasks.py
"""Celery-задачи apps.sync — обёртки над backfills/*.py.

Очередь 'default' (не 'interactive') — не конкурируют с OTP-письмами входа за
приоритет. Остались только служебные пересборки по данным самой БД и проверка
плана: загрузчики из Google-таблиц KOTOKOD в Eduson удалены — они искали
ученика по full_name, а у Eduson имена не уникальны (тёзки).
"""
from __future__ import annotations

from celery import shared_task

from apps.sync.backfills import (
    check_plan_health, rebuild_absence_resolutions, rebuild_counters, rebuild_payroll,
    rebuild_planned_lessons, rebuild_renewal_dates, rebuild_renewals,
)


@shared_task(name='apps.sync.tasks.rebuild_payroll_task', time_limit=180)
def rebuild_payroll_task(dry_run: bool = False) -> dict:
    return rebuild_payroll.run(dry_run=dry_run)


@shared_task(name='apps.sync.tasks.rebuild_counters_task', time_limit=180)
def rebuild_counters_task(dry_run: bool = False) -> dict:
    return rebuild_counters.run(dry_run=dry_run)


@shared_task(name='apps.sync.tasks.rebuild_planned_lessons_task', time_limit=300)
def rebuild_planned_lessons_task(dry_run: bool = False) -> dict:
    return rebuild_planned_lessons.run(dry_run=dry_run)


@shared_task(name='apps.sync.tasks.rebuild_absence_resolutions_task', time_limit=300)
def rebuild_absence_resolutions_task(dry_run: bool = False) -> dict:
    return rebuild_absence_resolutions.run(dry_run=dry_run)


@shared_task(name='apps.sync.tasks.rebuild_renewals_task', time_limit=300)
def rebuild_renewals_task(dry_run: bool = False) -> dict:
    return rebuild_renewals.run(dry_run=dry_run)


@shared_task(name='apps.sync.tasks.rebuild_renewal_dates_task', time_limit=300)
def rebuild_renewal_dates_task(dry_run: bool = False) -> dict:
    return rebuild_renewal_dates.run(dry_run=dry_run)


@shared_task(name='apps.sync.tasks.check_plan_health_task', time_limit=120)
def check_plan_health_task(dry_run: bool = False) -> dict:
    return check_plan_health.run(dry_run=dry_run)
