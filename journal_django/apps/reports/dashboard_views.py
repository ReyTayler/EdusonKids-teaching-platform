"""
Вьюхи дашборда «Посещения учеников» (спека 2026-09-15).

  GET /api/admin/reports/attendance-dashboard/summary   — две цифры
  GET /api/admin/reports/attendance-dashboard/students  — список учеников

Живут отдельно от views.py раздела: тот про фоновую генерацию Excel через
Celery, а здесь — обычное чтение агрегатов, общего у них только URL-префикс.

RBAC — IsManagerOrAdmin, как у всего раздела «Отчёты».
"""
from __future__ import annotations

from datetime import date

from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsManagerOrAdmin
from apps.reports import attendance_dashboard as ad


def _parse_period(request: Request) -> tuple[str, str]:
    """
    Период из query-строки. Обязателен и валидируется здесь, а не в агрегате:
    невалидная дата в SQL — это 500 вместо внятного 400, а пустой период молча
    дал бы нули и читался как «за месяц ничего не было».
    """
    raw_from = request.query_params.get('date_from') or ''
    raw_to = request.query_params.get('date_to') or ''
    if not raw_from or not raw_to:
        raise ValidationError({'error': 'date_from and date_to are required'})
    try:
        parsed_from = date.fromisoformat(raw_from)
        parsed_to = date.fromisoformat(raw_to)
    except ValueError:
        raise ValidationError({'error': 'date_from/date_to must be YYYY-MM-DD'})
    if parsed_to < parsed_from:
        raise ValidationError({'error': 'date_to must not be earlier than date_from'})
    return parsed_from.isoformat(), parsed_to.isoformat()


class AttendanceDashboardSummaryView(APIView):
    """Две цифры: отработано за всё время и за период. По всей школе."""

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        date_from, date_to = _parse_period(request)
        return Response(ad.summary(date_from, date_to))


class AttendanceDashboardStudentsView(APIView):
    """
    Список учеников с разбивкой по типам занятий внутри периода.

    Сортировка: sort_by ∈ attendance_dashboard.ORDERING_FIELDS, sort_dir ∈
    asc|desc; мусор → 400, как в остальных списках. Фильтр по имени —
    filter[full_name], подстрока без учёта регистра.
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        date_from, date_to = _parse_period(request)
        qp = request.query_params
        sort_by = qp.get('sort_by') or ad.DEFAULT_SORT_BY
        sort_dir = qp.get('sort_dir') or ad.DEFAULT_SORT_DIR
        if sort_by not in ad.ORDERING_FIELDS:
            raise ValidationError(
                f"Invalid sort_by '{sort_by}'. Allowed: {sorted(ad.ORDERING_FIELDS)}"
            )
        if sort_dir not in ('asc', 'desc'):
            raise ValidationError(
                f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'."
            )

        qs = ad.student_rows_queryset(
            date_from, date_to, sort_by, sort_dir,
            name_query=qp.get('filter[full_name]') or '',
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(ad.serialize_rows(page))
