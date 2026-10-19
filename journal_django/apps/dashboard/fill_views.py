"""
Тонкая APIView вкладки «Заполнить» дашборда.

  GET /api/admin/dashboard/unfilled-lessons?teacher_id=&sort_dir=&page=&page_size=
      → пагинированный {rows,total,page,page_size} просроченных незаполненных
        плановых + доп.уроков по школе (опц. один преподаватель).
        sort_dir: 'desc' (по умолчанию, новые просрочки сверху) | 'asc'.

Права: manager/admin (IsManagerOrAdmin) — как весь дашборд. Логика — в
fill_service; здесь только валидация teacher_id/sort_dir и пагинация готового
списка (StandardPagination штатно работает и с Python-списком).
"""
from __future__ import annotations

from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsManagerOrAdmin
from apps.dashboard import fill_service

_MAX_INT4 = 2147483647
_SORT_DIRS = {'asc', 'desc'}


class UnfilledLessonsView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        raw = request.query_params.get('teacher_id')
        teacher_id = None
        if raw:
            # isdecimal (не isdigit): '²' пройдёт isdigit, но упадёт в int() → 500.
            if not raw.isdecimal() or int(raw) > _MAX_INT4:
                raise ValidationError('teacher_id должен быть целым числом.')
            teacher_id = int(raw)

        sort_dir = request.query_params.get('sort_dir', 'desc')
        if sort_dir not in _SORT_DIRS:
            raise ValidationError(f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'.")

        rows = fill_service.unfilled_lessons(teacher_id, sort_dir)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(page)
