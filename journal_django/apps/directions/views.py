"""
DirectionsView — тонкий ViewSet для /api/admin/directions.

Зеркалит Express routes/admin/directions.js:
  GET    /api/admin/directions        → list()     → 200 [...]
  GET    /api/admin/directions/:id    → retrieve() → 200 | 404
  POST   /api/admin/directions        → create()   → 201 | 409
  PATCH  /api/admin/directions/:id    → update()   → 200 | 404
  DELETE /api/admin/directions/:id    → destroy()  → 204 | 404 | 409 (has_payments)

Параметры: ?include_inactive=1 (GET список).
Права: чтение — manager/admin/superadmin; запись — только superadmin (ReadStaffWriteSuperAdmin).

DELETE: если у направления есть оплаты — 409 {error: 'has_payments', ...}.
"""
from __future__ import annotations

from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ReadStaffWriteSuperAdmin
from apps.directions import services
from apps.directions.serializers import DirectionUpdateSerializer, DirectionWriteSerializer


class DirectionListCreateView(APIView):
    """
    GET  /api/admin/directions  — список направлений
    POST /api/admin/directions  — создать направление
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request) -> Response:
        include_inactive = request.query_params.get('include_inactive') == '1'
        rows = services.list_directions(include_inactive=include_inactive)
        return Response(rows)

    def post(self, request: Request) -> Response:
        serializer = DirectionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            direction = services.create_direction(serializer.validated_data)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response(
                    {'error': 'Already exists'},
                    status=status.HTTP_409_CONFLICT,
                )
            raise

        return Response(direction, status=status.HTTP_201_CREATED)


class DirectionDetailView(APIView):
    """
    GET    /api/admin/directions/:id  — получить направление
    PATCH  /api/admin/directions/:id  — обновить направление
    DELETE /api/admin/directions/:id  — мягкое удаление (с проверкой payments)
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request, pk: int) -> Response:
        direction = services.get_direction(pk)
        if direction is None:
            raise NotFound({'error': 'Not found'})
        return Response(direction)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = DirectionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated = services.update_direction(pk, serializer.validated_data)
        if updated is None:
            raise NotFound({'error': 'Not found'})

        return Response(updated)

    def delete(self, request: Request, pk: int) -> Response:
        # Проверяем наличие оплат — дословно из routes/admin/directions.js
        count = services.get_direction_payments_count(pk)
        if count > 0:
            return Response(
                {
                    'error': 'has_payments',
                    'message': f'У направления есть {count} оплат. Удалить нельзя.',
                    'payments_count': count,
                },
                status=status.HTTP_409_CONFLICT,
            )

        ok = services.soft_delete_direction(pk)
        if not ok:
            raise NotFound({'error': 'Not found'})

        return Response(status=status.HTTP_204_NO_CONTENT)


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
