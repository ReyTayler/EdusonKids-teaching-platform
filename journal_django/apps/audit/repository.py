"""
AuditRepository — единственное место доступа к данным раздела audit.

ORM-порт services/repo/audit.js + writer logEvent (services/audit.js).

Контракт пагинации: { rows, total, page, page_size } — как paginate() в Express.
Sortable: occurred_at (default DESC), event.
Filters: event (exact), account_id (exact), actor_email (LIKE, регистронезависимо).

SELECT эквивалент: l.*, a.email AS account_email
FROM security_audit_log l LEFT JOIN accounts a ON a.id = l.account_id
"""
from __future__ import annotations

from typing import Any, Optional

from django.db.models import F
from django.db.models.functions import Now

from apps.core.utils.orm import dictrows

from .models import SecurityAuditLog


# ---------------------------------------------------------------------------
# Конфигурация пагинации (дословно из AUDIT_PAGINATION)
# ---------------------------------------------------------------------------

# Маппинг sort_by → ORM-поле. l.id DESC — вторичная сортировка.
_SORTABLE: dict[str, str] = {
    'occurred_at': 'occurred_at',
    'event':       'event',
}

_DEFAULT_SORT_BY = 'occurred_at'
_DEFAULT_SORT_DIR = 'desc'


def _apply_filters(qs, filters: dict[str, Any]):
    """
    Применяет фильтры (зеркалит F.*-билдеры AUDIT_PAGINATION.filters):
      event:        exact  → event = value
      account_id:   num    → account_id = int(value)
      actor_email:  like   → LOWER(actor_email) LIKE %lower% (icontains)
    """
    event = filters.get('event')
    if event not in (None, ''):
        qs = qs.filter(event=str(event))

    account_id = filters.get('account_id')
    if account_id not in (None, ''):
        qs = qs.filter(account_id=int(account_id))

    actor_email = filters.get('actor_email')
    if actor_email not in (None, ''):
        qs = qs.filter(actor_email__icontains=str(actor_email))

    return qs


# ---------------------------------------------------------------------------
# Repository functions
# ---------------------------------------------------------------------------

def list_audit(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = _DEFAULT_SORT_BY,
    sort_dir: str = _DEFAULT_SORT_DIR,
    filters: Optional[dict] = None,
) -> dict:
    """
    Возвращает пагинированный список записей аудита.

    Контракт ответа: { rows, total, page, page_size } — дословно как paginate().
    id (bigint) отдаётся строкой — паритет с node-postgres (int8 → string).
    """
    if filters is None:
        filters = {}

    sort_field = _SORTABLE.get(sort_by) or _SORTABLE[_DEFAULT_SORT_BY]
    order_prefix = '' if sort_dir == 'asc' else '-'

    qs = _apply_filters(SecurityAuditLog.objects.all(), filters)

    total = qs.count()  # COUNT(*) — LEFT JOIN на accounts не меняет число строк

    offset = max(0, (page - 1) * page_size)
    ordered = qs.order_by(f'{order_prefix}{sort_field}', '-id')
    rows = dictrows(
        ordered[offset:offset + page_size].values(
            'id', 'occurred_at', 'account_id', 'actor_email', 'event',
            'ip', 'user_agent', 'target_id', 'meta',
            account_email=F('account__email'),   # LEFT JOIN accounts
        )
    )
    # l.id::text — int8 у node-postgres приходит строкой, повторяем.
    for row in rows:
        row['id'] = str(row['id'])

    return {
        'rows': rows,
        'total': total,
        'page': page,
        'page_size': page_size,
    }


# ---------------------------------------------------------------------------
# Writer — порт services/audit.js logEvent (INSERT в security_audit_log)
# ---------------------------------------------------------------------------

def insert_event(
    event: str,
    account_id=None,
    actor_email=None,
    ip=None,
    user_agent=None,
    target_id=None,
    meta=None,
) -> None:
    """
    INSERT записи в security_audit_log. Порт logEvent (services/audit.js).

    meta — dict/None; пишется в jsonb-колонку (TolerantJSONField).
    occurred_at — DB DEFAULT now() через Now().
    Вызывающий обязан санитизировать секреты ДО передачи (services.log_event).
    """
    SecurityAuditLog.objects.create(
        account_id=account_id,
        actor_email=actor_email,
        event=event,
        ip=ip,
        user_agent=user_agent,
        target_id=target_id,
        meta=meta,
        occurred_at=Now(),
    )
