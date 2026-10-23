"""
API справочника причин отмены: /api/admin/cancellation-reasons.

  GET    ''      → {"teacher": [Row], "client": [Row]} — активные, ORDER BY position, id
  POST   ''      → 201 Row
  PATCH  '/<id>' → 200 Row | 404 (нет или удалена)
  DELETE '/<id>' → 204 | 404 — мягкое удаление

Вкладка группы «Отмены уроков» (маршрут — apps/groups/urls.py, рядом с планом):
  GET /api/admin/groups/<pk>/cancellations → StandardPagination {rows,total,page,page_size},
      порядок фиксирован (lesson_date, scheduled_time, id — DESC); 404 — нет группы.

Права: чтение — manager/admin/superadmin (форма отмены урока), запись — только
superadmin (ReadStaffWriteSuperAdmin). Ключ compensation в Row — только для
superadmin: он решает о деньгах преподавателя (правило мотивации, В3).
"""
from __future__ import annotations

from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cancellations import repository, services
from apps.cancellations.serializers import (
    GroupCancellationRowSerializer, ReasonCreateSerializer, ReasonUpdateSerializer,
)
from apps.core.pagination import StandardPagination
from apps.core.permissions import IsManagerOrAdmin, ReadStaffWriteSuperAdmin
from apps.groups.models import Group


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


class GroupCancellationListView(generics.ListAPIView):
    """Отмены уроков группы, новые сверху. Только чтение: правки отмены нет
    (решение владельца), ошибка в деньгах чинится ручной суммой в «Зарплате».

    Сортировки из query-строки нет: порядок фиксирован, whitelist не нужен."""
    permission_classes = [IsManagerOrAdmin]
    pagination_class = StandardPagination
    serializer_class = GroupCancellationRowSerializer

    def list(self, request: Request, *args, **kwargs) -> Response:
        # Пустая страница у несуществующей группы выглядела бы как «отмен не
        # было» — опечатка в id должна давать 404, как у карточки группы.
        if not Group.objects.filter(pk=self.kwargs['pk']).exists():
            raise NotFound({'error': 'Not found'})
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        return repository.list_for_group(self.kwargs['pk'])
