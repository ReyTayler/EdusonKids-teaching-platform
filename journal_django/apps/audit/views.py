"""
AuditView — ViewSet для /api/admin/audit-log.

Зеркалит Express routes/admin/audit.js:
  GET /api/admin/audit-log → list() → 200 { rows, total, page, page_size }

ТОЛЬКО GET — запись через services/audit.js (logEvent).
Права: ТОЛЬКО superadmin (IsSuperAdmin) — не admin, не manager.
Сортировка по умолчанию: occurred_at DESC.

Допустимые sort_by: occurred_at, event.
"""
from __future__ import annotations

from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsSuperAdmin
from apps.audit import services

# Whitelist sort_by
ORDERING_FIELDS = ['occurred_at', 'event']


def _parse_list_params(request: Request) -> dict:
    """
    Извлечь параметры пагинации из query string.

    Defaults: sortBy='occurred_at', sortDir='desc' — как в Express parsePaginationRequest
    с переопределёнными defaults в routes/admin/audit.js.
    """
    qp = request.query_params

    page = max(1, int(qp.get('page', 1) or 1))
    page_size = min(500, max(1, int(qp.get('page_size', 50) or 50)))

    sort_by = qp.get('sort_by', 'occurred_at') or 'occurred_at'
    sort_dir = qp.get('sort_dir', 'desc') or 'desc'

    if sort_by not in ORDERING_FIELDS:
        raise ValidationError(
            f"Invalid sort_by '{sort_by}'. Allowed: {ORDERING_FIELDS}"
        )
    if sort_dir not in ('asc', 'desc'):
        raise ValidationError(
            f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'."
        )

    filters: dict = {}
    for key, value in qp.items():
        if key.startswith('filter[') and key.endswith(']'):
            filter_key = key[7:-1]
            filters[filter_key] = value

    return {
        'page': page,
        'page_size': page_size,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'filters': filters,
    }


class AuditLogListView(APIView):
    """
    GET /api/admin/audit-log — список записей аудита с пагинацией.
    """

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request) -> Response:
        params = _parse_list_params(request)
        result = services.list_audit(**params)
        return Response(result)
