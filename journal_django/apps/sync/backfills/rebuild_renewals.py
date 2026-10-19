# journal_django/apps/sync/backfills/rebuild_renewals.py
"""Полный пересбор всех сделок продления из истории посещаемости.

Тонкая обёртка над apps.renewals.rebuild.rebuild_all. dry_run=true — только план
(счётчики + примеры), ничего не пишет. apply — атомарно сносит все сделки и
записывает заново (см. apps/renewals/rebuild.py и спеку 2026-07-19)."""
from __future__ import annotations

from apps.renewals import rebuild


def run(dry_run: bool = False) -> dict:
    return rebuild.rebuild_all(dry_run=dry_run)
