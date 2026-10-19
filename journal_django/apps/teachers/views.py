"""
TeachersView — тонкий ViewSet для /api/admin/teachers.

Зеркалит Express routes/admin/teachers.js:
  GET    /api/admin/teachers        → list()           → 200 [...]
  GET    /api/admin/teachers/:id    → retrieve()       → 200 | 404
  POST   /api/admin/teachers        → create()         → 201 | 409
  PATCH  /api/admin/teachers/:id    → partial_update() → 200 | 404
  DELETE /api/admin/teachers/:id    → destroy()        → 204 | 404

Параметры: ?include_inactive=1 (GET список).
Права: чтение — manager/admin/superadmin; запись — только superadmin (ReadStaffWriteSuperAdmin).
"""
from __future__ import annotations

import re

from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsManagerOrAdmin, ReadStaffWriteSuperAdmin
from apps.core.utils.dates import msk_now
from apps.teachers import services
from apps.teachers.serializers import TeacherUpdateSerializer, TeacherWriteSerializer


class TeacherListCreateView(APIView):
    """
    GET  /api/admin/teachers  — список преподавателей
    POST /api/admin/teachers  — создать преподавателя
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request) -> Response:
        include_inactive = request.query_params.get('include_inactive') == '1'
        rows = services.list_teachers(include_inactive=include_inactive)
        return Response(rows)

    def post(self, request: Request) -> Response:
        serializer = TeacherWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            teacher = services.create_teacher(serializer.validated_data)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response(
                    {'error': 'Already exists'},
                    status=status.HTTP_409_CONFLICT,
                )
            raise

        return Response(teacher, status=status.HTTP_201_CREATED)


class TeacherDetailView(APIView):
    """
    GET    /api/admin/teachers/:id  — получить преподавателя
    PATCH  /api/admin/teachers/:id  — обновить преподавателя
    DELETE /api/admin/teachers/:id  — мягкое удаление
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request, pk: int) -> Response:
        teacher = services.get_teacher(pk)
        if teacher is None:
            raise NotFound({'error': 'Not found'})
        return Response(teacher)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = TeacherUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated = services.update_teacher(pk, serializer.validated_data)
        if updated is None:
            raise NotFound({'error': 'Not found'})

        return Response(updated)

    def delete(self, request: Request, pk: int) -> Response:
        ok = services.soft_delete_teacher(pk)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)


# Строгий формат месяца: '2026-7' и '2026-13' обязаны отваливаться на входе, а не
# превращаться в пустой период молча.
#
# Год ограничен 19xx–20xx намеренно. Без рамки регексп пропускал '9999-12' и
# '0001-01', на которых month_bounds считает date(10000, 1, 1) / date(0, ...) и
# падает ValueError — то есть провалидированный вход давал 500 вместо 400.
_MONTH_RE = re.compile(r'^(19|20)\d{2}-(0[1-9]|1[0-2])$')


class TeacherStatsView(APIView):
    """
    GET /api/admin/teachers/:id/stats?month=YYYY-MM — показатели преподавателя.

    Read-only, поэтому IsManagerOrAdmin, а не ReadStaffWriteSuperAdmin:
    менеджеру статистика нужна, а писать здесь нечего.
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        if services.get_teacher(pk) is None:
            raise NotFound({'error': 'Not found'})

        month = request.query_params.get('month') or msk_now().strftime('%Y-%m')
        if not _MONTH_RE.match(month):
            return Response(
                {'error': f"Invalid month '{month}', expected YYYY-MM"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Зарплата — только суперадмину: раздел «Зарплата» закрыт IsSuperAdmin
        # (apps/payroll/views.py), а эту карточку видит и менеджер. Роль берём
        # из JWT, не из параметра запроса.
        with_payroll = getattr(request.user, 'role', None) == 'superadmin'
        return Response(services.get_teacher_stats(pk, month, with_payroll=with_payroll))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_unique_violation(exc: Exception) -> bool:
    pgcode = getattr(exc, 'pgcode', None)
    if pgcode == '23505':
        return True
    cause = getattr(exc, '__cause__', None)
    if cause and getattr(cause, 'pgcode', None) == '23505':
        return True
    return False
