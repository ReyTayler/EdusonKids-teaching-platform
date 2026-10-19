"""Админские вьюхи: справочник аккаунтов, привязка, раздел «Уведомления»."""
from __future__ import annotations

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsAdminOrSuperAdmin, ReadStaffWriteAdmin
from apps.notifications import repository
from apps.notifications.constants import KIND_FILL_DIGEST, KIND_MORNING_DIGEST
from apps.notifications.models import (
    NotificationSettings, TelegramRecipient, TelegramUser,
)
from apps.notifications.serializers import NotificationMessageSerializer
from apps.teachers.models import Teacher


class TelegramUsersView(APIView):
    """
    GET /api/admin/telegram-users — аккаунты, известные боту (для выбора при привязке).

    Права те же, что у самой привязки (`TeacherTelegramView` — admin/superadmin),
    а не `IsManagerOrAdmin`: список существует ТОЛЬКО чтобы выбрать аккаунт при
    привязке, а привязывать менеджер не может. Пока стояло IsManagerOrAdmin,
    менеджер, открывая любую карточку преподавателя, выкачивал имена, ники и
    chat_id всех, кто когда-либо писал боту, вместе с тем, к кому они привязаны —
    выпадашки он не видел, но данные получал.
    """

    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request: Request) -> Response:
        rows = list(
            TelegramUser.objects
            .order_by('full_name')
            .values('chat_id', 'username', 'full_name')
        )
        bound = dict(
            TelegramRecipient.objects
            .values_list('telegram_user__chat_id', 'teacher__name')
        )
        for row in rows:
            row['bound_to'] = bound.get(row['chat_id'])
        return Response({'rows': rows, 'total': len(rows)})


class TeacherTelegramView(APIView):
    """POST/DELETE /api/admin/teachers/<id>/telegram — привязка и отвязка."""

    permission_classes = [ReadStaffWriteAdmin]

    def post(self, request: Request, teacher_id: int) -> Response:
        if not Teacher.objects.filter(id=teacher_id).exists():
            raise NotFound('Преподаватель не найден.')

        chat_id = request.data.get('chat_id')
        if not chat_id:
            raise ValidationError('chat_id обязателен.')
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            raise ValidationError('chat_id должен быть целым числом.')

        try:
            tg_user = TelegramUser.objects.get(chat_id=chat_id)
        except TelegramUser.DoesNotExist:
            raise NotFound('Аккаунт неизвестен боту. Попросите написать боту /start.')

        recipient, _created = TelegramRecipient.objects.update_or_create(
            teacher_id=teacher_id,
            defaults={
                'telegram_user': tg_user,
                # Повторная привязка снимает пометку блокировки: человек мог
                # разблокировать бота, и признак должен обнулиться.
                'is_active': True,
                'blocked_reason': None,
            },
        )
        return Response({
            'chat_id': tg_user.chat_id,
            'username': tg_user.username,
            'full_name': tg_user.full_name,
            'is_active': recipient.is_active,
        })

    def delete(self, request: Request, teacher_id: int) -> Response:
        TelegramRecipient.objects.filter(teacher_id=teacher_id).delete()
        return Response(status=204)


class NotificationListView(APIView):
    """GET /api/admin/notifications — журнал доставки."""

    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request: Request) -> Response:
        qs = repository.filtered(
            kind=request.query_params.get('kind'),
            channel=request.query_params.get('channel'),
            status=request.query_params.get('status'),
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = NotificationMessageSerializer(page, many=True).data
        return paginator.get_paginated_response(data)


class NotificationScheduleView(APIView):
    """GET /api/admin/notifications/schedule — вкладка «Расписание»."""

    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request: Request) -> Response:
        last = repository.last_runs()
        return Response({
            'jobs': [
                {'key': 'morning_digest', 'title': 'Утренний дайджест расписания',
                 'schedule': '08:00', 'kind': KIND_MORNING_DIGEST,
                 'last_run_at': last['morning_digest']},
                {'key': 'fill_digest', 'title': 'Незаполненные отчёты',
                 'schedule': '21:00', 'kind': KIND_FILL_DIGEST,
                 'last_run_at': last['fill_digest']},
                {'key': 'dispatch', 'title': 'Отправка очереди',
                 'schedule': 'каждую минуту', 'kind': None,
                 'last_run_at': last['dispatch']},
            ],
            'counts': repository.counts_by_status(),
        })


class NotificationToggleView(APIView):
    """
    GET/POST /api/admin/notifications/toggle — общешкольный выключатель рассылки.

    Выключено = сообщения не создаются вовсе (ни точечные, ни дайджесты) и уже
    стоящие в очереди не отправляются. При включении ничего не хлынет пачкой.
    """

    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request: Request) -> Response:
        return Response(self._state())

    def post(self, request: Request) -> Response:
        value = request.data.get('is_enabled')
        if not isinstance(value, bool):
            raise ValidationError('is_enabled должен быть true или false.')

        row = NotificationSettings.load()
        row.is_enabled = value
        user = request.user if request.user.is_authenticated else None
        row.updated_by = (user.full_name or user.email) if user else None
        row.save(update_fields=['is_enabled', 'updated_by', 'updated_at'])
        return Response(self._state())

    @staticmethod
    def _state() -> dict:
        row = NotificationSettings.load()
        return {
            'is_enabled': row.is_enabled,
            'updated_at': row.updated_at,
            'updated_by': row.updated_by,
        }
