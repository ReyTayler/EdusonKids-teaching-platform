# journal_django/apps/sync/backfills/rebuild_renewal_dates.py
"""Недеструктивное восстановление реальных дат стадий открытых сделок продления.

Тонкая обёртка над apps.renewals.rebuild.backfill_open_dates. В отличие от
полного пересбора (rebuild_renewals) НЕ сносит сделки и НЕ трогает
стадии/ответственных/комментарии — только проставляет открытым авто-сделкам
реальную stage_entered_at из истории посещаемости (после массового пересбора у
них стояла дата пересбора). dry_run=true — только план (счётчики + примеры)."""
from __future__ import annotations

from apps.renewals import rebuild


def run(dry_run: bool = False) -> dict:
    return rebuild.backfill_open_dates(dry_run=dry_run)
