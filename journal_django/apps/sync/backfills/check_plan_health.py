# journal_django/apps/sync/backfills/check_plan_health.py
"""Проверка здоровья планов занятий по всем активным группам.

ТОЛЬКО ЧТЕНИЕ — в отличие от остальных модулей apps/sync/backfills, ничего не
пишет. Логика проверок живёт в apps.scheduling.health (доменное знание о плане),
здесь только обёртка под общий контракт run(dry_run) → dict, которого ждёт
apps/sync/views.py. Параметр dry_run принимается и игнорируется: менять нечего.

Спека: docs/superpowers/specs/2026-08-05-plan-health-design.md §4.
"""
from __future__ import annotations

from apps.scheduling import health


def run(dry_run: bool = False) -> dict:
    return health.check_all()
