"""
AuditService — тонкий слой между views и repository.

Никакого SQL здесь — всё через repository.
Также содержит writer log_event (порт services/audit.js): запись событий
безопасности с САНИТИЗАЦИЕЙ секретов. Аудит не должен ронять запрос —
ошибки записи только логируются.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from rest_framework.request import Request

from apps.audit import repository

logger = logging.getLogger('audit')

# Ключи-секреты, которые НИКОГДА не попадают в meta (порт SECRET_KEYS из audit.js).
SECRET_KEYS = frozenset({'password', 'code', 'twofa_secret', 'token', 'password_hash', 'recovery'})


def sanitize_meta(meta):
    """Поверхностно вырезать ключи-секреты из meta. Порт sanitizeMeta (audit.js)."""
    if not isinstance(meta, dict):
        return meta if meta is not None else None
    return {k: v for k, v in meta.items() if k not in SECRET_KEYS}


def _client_ip(request: Optional[Request]):
    """ip из X-Forwarded-For или REMOTE_ADDR. Порт audit.js (req.headers/socket)."""
    if request is None:
        return None
    meta = request.META
    return meta.get('HTTP_X_FORWARDED_FOR') or meta.get('REMOTE_ADDR') or None


def log_event(
    event: str,
    account_id=None,
    actor_email=None,
    target_id=None,
    meta=None,
    request: Optional[Request] = None,
) -> None:
    """
    Записать событие безопасности. Порт logEvent (services/audit.js).

    Секреты вырезаются sanitize_meta. Ошибки записи не пробрасываются (аудит
    не должен ронять основной запрос) — только лог в stderr.
    """
    ip = _client_ip(request)
    ua = request.META.get('HTTP_USER_AGENT') if request is not None else None
    try:
        # Savepoint: при ошибке INSERT (например FK на несуществующий actor)
        # откатывается только вложенный savepoint, а внешняя транзакция запроса
        # остаётся пригодной. Без этого пойманное исключение всё равно «отравляет»
        # окружающий atomic-блок → TransactionManagementError на следующем запросе.
        with transaction.atomic():
            repository.insert_event(
                event=event,
                account_id=account_id,
                actor_email=actor_email,
                ip=ip,
                user_agent=ua,
                target_id=target_id,
                meta=sanitize_meta(meta),
            )
    except Exception as exc:  # noqa: BLE001 — аудит не должен ронять запрос
        logger.error('[audit] failed to log %s: %s', event, exc)


def list_audit(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = 'occurred_at',
    sort_dir: str = 'desc',
    filters: Optional[dict] = None,
) -> dict:
    """Делегирует пагинированный список аудита в repository."""
    return repository.list_audit(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filters=filters,
    )
