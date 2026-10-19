"""
Тонкие APIView для /api/admin/dashboard.

Зеркалит Express routes/admin/dashboard.js:
  GET /api/admin/dashboard          → сводка (params from/to)  | 400 {error:'invalid_date'}
  GET /api/admin/dashboard/revenue  → Revenue/Orders + по дням/месяцам (params from/to)
                                      | 400 {error:'invalid_date'|'invalid_range'}

Права: только manager или admin (IsManagerOrAdmin).
Валидация дат — дословный порт (isValidIsoDate).
"""
from __future__ import annotations

import datetime
import re

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsManagerOrAdmin
from apps.dashboard import services

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _is_valid_iso_date(v) -> bool:
    """
    Формат YYYY-MM-DD + реальная календарная дата. Порт dashboard.js isValidIsoDate
    (отсекает 2026-13-99, 2026-02-30 и non-string из ?from[]=).
    """
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return False
    try:
        datetime.date.fromisoformat(v)
        return True
    except ValueError:
        return False


class DashboardView(APIView):
    """GET /api/admin/dashboard — финансовая сводка."""

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        from_ = request.query_params.get('from')
        to = request.query_params.get('to')
        if (from_ and not _is_valid_iso_date(from_)) or (to and not _is_valid_iso_date(to)):
            return Response({'error': 'invalid_date'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(services.get_dashboard_cached(from_=from_ or None, to=to or None))


class DashboardRevenueView(APIView):
    """GET /api/admin/dashboard/revenue — поступления за период по дням и месяцам."""

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        from_ = request.query_params.get('from')
        to = request.query_params.get('to')
        if (from_ and not _is_valid_iso_date(from_)) or (to and not _is_valid_iso_date(to)):
            return Response({'error': 'invalid_date'}, status=status.HTTP_400_BAD_REQUEST)
        if from_ and to and from_ > to:
            return Response({'error': 'invalid_range'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(services.get_revenue_cached(from_=from_ or None, to=to or None))
