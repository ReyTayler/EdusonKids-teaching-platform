"""
MembershipsView — тонкие APIView для /api/admin/memberships.

Зеркалит Express routes/admin/memberships.js:
  GET    /api/admin/memberships     → список (без пагинации!) → 200 []
  POST   /api/admin/memberships     → upsert → 201
  PATCH  /api/admin/memberships/:id → обновить → 200 | 404
  DELETE /api/admin/memberships/:id → soft-delete (active=false) → 204 | 404

Права: чтение — manager/admin/superadmin.
Запись:
  POST /memberships          (добавить в группу)  — manager/admin/superadmin
  DELETE /memberships/:id    (убрать из группы)   — manager/admin/superadmin
  PATCH /memberships/:id     (правка lessons_done/start_date/active) — только superadmin
  POST /memberships/:id/transfer (перевод в другую группу)           — только superadmin
Решение 2026-07-28: набор группы — рутина менеджера, а ручная правка счётчиков
и перевод между группами двигают прогресс/деньги и остаются за суперадмином.

Фильтры: group_id, student_id (числа), include_inactive ('1' → True).
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsManagerOrAdmin, ReadStaffWriteSuperAdmin
from apps.extra_lessons.exceptions import MembershipHasScheduledMakeups
from apps.memberships import services
from apps.memberships.exceptions import (
    DirectionMismatch,
    IndividualGroupFull,
    SameGroupTransfer,
    TargetGroupUnavailable,
)
from apps.memberships.serializers import (
    MembershipTransferSerializer,
    MembershipUpdateSerializer,
    MembershipWriteSerializer,
)

# Код конфликта для фронта: снятие членства заблокировано назначенными доп.уроками
# (фронт показывает модалку по этому коду). str(e) — человеко-читаемое сообщение.
_SCHEDULED_MAKEUPS_CODE = 'membership_has_scheduled_makeups'


def _parse_int_param(qp, key: str):
    val = qp.get(key)
    if not val:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        raise ValidationError({key: 'Must be an integer.'})


class MembershipListCreateView(APIView):
    """
    GET  /api/admin/memberships  — список без пагинации
    POST /api/admin/memberships  — UPSERT membership (добавление ученика в группу)
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        qp = request.query_params

        group_id = _parse_int_param(qp, 'group_id')
        student_id = _parse_int_param(qp, 'student_id')
        include_inactive = qp.get('include_inactive') == '1'

        result = services.list_memberships(
            group_id=group_id,
            student_id=student_id,
            include_inactive=include_inactive,
        )
        return Response(result)

    def post(self, request: Request) -> Response:
        serializer = MembershipWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            membership = services.add_membership(serializer.validated_data)
        except IndividualGroupFull as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(membership, status=status.HTTP_201_CREATED)


class MembershipDetailView(APIView):
    """
    PATCH  /api/admin/memberships/:id  — обновить membership (superadmin)
    DELETE /api/admin/memberships/:id  — мягкое удаление (manager и выше)
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def get_permissions(self) -> list[BasePermission]:
        # Штатный DRF-хук для разных прав на разные методы одной вьюхи:
        # «убрать из группы» доступно менеджеру, ручная правка полей — нет.
        if self.request.method == 'DELETE':
            return [IsManagerOrAdmin()]
        return super().get_permissions()

    def patch(self, request: Request, pk: int) -> Response:
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated = services.update_membership(pk, serializer.validated_data)
        except IndividualGroupFull as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except MembershipHasScheduledMakeups as e:
            return Response({'error': str(e), 'code': _SCHEDULED_MAKEUPS_CODE},
                            status=status.HTTP_409_CONFLICT)
        if updated is None:
            raise NotFound({'error': 'Not found'})

        return Response(updated)

    def delete(self, request: Request, pk: int) -> Response:
        try:
            ok = services.remove_membership(pk)
        except MembershipHasScheduledMakeups as e:
            return Response({'error': str(e), 'code': _SCHEDULED_MAKEUPS_CODE},
                            status=status.HTTP_409_CONFLICT)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembershipTransferView(APIView):
    """
    POST /api/admin/memberships/:id/transfer — перевод ученика в другую
    активную группу того же направления (см. services.transfer_membership).
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        serializer = MembershipTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated = services.transfer_membership(pk, serializer.validated_data['to_group_id'])
        except IndividualGroupFull as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except MembershipHasScheduledMakeups as e:
            return Response({'error': str(e), 'code': _SCHEDULED_MAKEUPS_CODE},
                            status=status.HTTP_409_CONFLICT)
        except (DirectionMismatch, SameGroupTransfer, TargetGroupUnavailable) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if updated is None:
            raise NotFound({'error': 'Not found'})

        return Response(updated)


