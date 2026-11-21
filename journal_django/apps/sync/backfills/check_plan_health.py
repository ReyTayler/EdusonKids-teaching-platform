# journal_django/apps/sync/backfills/check_plan_health.py
"""Проверка здоровья планов занятий по всем активным группам.

ТОЛЬКО ЧТЕНИЕ. Логика проверок живёт в apps.scheduling.health (доменное знание
о плане); здесь обёртка run(dry_run) → dict, оставшаяся после удаления раздела
«Синхро» (2026-11-21): вызывающих в коде нет, сверка импорта зовёт
health.check_all напрямую. Параметр dry_run игнорируется.

Спека: docs/superpowers/specs/2026-08-05-plan-health-design.md §4.
"""
from __future__ import annotations

from apps.scheduling import health


def run(dry_run: bool = False) -> dict:
    return health.check_all()
