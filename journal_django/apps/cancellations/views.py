"""
API справочника причин отмены: /api/admin/cancellation-reasons.

  GET    ''      → {"teacher": [Row], "client": [Row]} — активные, ORDER BY position, id
  POST   ''      → 201 Row
  PATCH  '/<id>' → 200 Row | 404 (нет или удалена)
  DELETE '/<id>' → 204 | 404 — мягкое удаление

Права: чтение — manager/admin/superadmin (форма отмены урока), запись — только
superadmin (ReadStaffWriteSuperAdmin). Ключ compensation в Row — только для
superadmin: он решает о деньгах преподавателя (правило мотивации, В3).
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cancellations import services
from apps.cancellations.serializers import ReasonCreateSerializer, ReasonUpdateSerializer
from apps.core.permissions import ReadStaffWriteSuperAdmin


def _nothing_to_change() -> Response:
    return Response({'error': services.NOTHING_TO_CHANGE}, status=status.HTTP_400_BAD_REQUEST)


def _sees_compensation(request: Request) -> bool:
    return getattr(request.user, 'role', None) == 'superadmin'


class CancellationReasonListView(APIView):
    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.list_reasons(with_compensation=_sees_compensation(request)))

    def post(self, request: Request) -> Response:
        serializer = ReasonCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = services.create_reason(**serializer.validated_data)
        return Response(
            services.serialize_reason(reason, with_compensation=_sees_compensation(request)),
            status=status.HTTP_201_CREATED,
        )


class CancellationReasonDetailView(APIView):
    permission_classes = [ReadStaffWriteSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        # {} и null — «нечего менять»; null сериализатор отверг бы общим
        # «Invalid data», а клиенту нужен один понятный ответ на оба случая.
        if not request.data:
            return _nothing_to_change()
        serializer = ReasonUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reason = services.update_reason(pk, dict(serializer.validated_data))
        except services.CancellationInvalid as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if reason is None:
            raise NotFound({'error': 'Not found'})
        return Response(
            services.serialize_reason(reason, with_compensation=_sees_compensation(request)))

    def delete(self, request: Request, pk: int) -> Response:
        if not services.delete_reason(pk):
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)
