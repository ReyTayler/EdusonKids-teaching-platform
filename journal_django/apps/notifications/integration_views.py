"""
Служебные эндпоинты для Telegram-бота.

Закрыты общим секретом И правилом nginx (allow 127.0.0.1; deny all) — бот и
журнал на одной машине, наружу этому торчать незачем.
"""
from __future__ import annotations

import datetime

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.utils.dates import msk_now
from apps.notifications.authentication import BotServiceAuthentication, IsBotService
from apps.notifications.models import TelegramRecipient, TelegramUser
from apps.scheduling.services import build_calendar


def _parse_telegram_id(raw) -> int:
    """int(...) на пользовательском вводе от бота — мусор → 400, а не 500."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError('telegram_id должен быть целым числом.')


class TelegramIdentifyView(APIView):
    """POST /api/integrations/telegram/identify — «этому chat_id соответствует такой ник»."""

    authentication_classes = [BotServiceAuthentication]
    permission_classes = [IsBotService]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'bot_service'

    def post(self, request: Request) -> Response:
        telegram_id = _parse_telegram_id(request.data.get('telegram_id'))
        full_name = (request.data.get('full_name') or '').strip()
        if not full_name:
            raise ValidationError('full_name обязателен.')

        username = (request.data.get('username') or '').lstrip('@') or None
        TelegramUser.objects.update_or_create(
            chat_id=telegram_id,
            defaults={'username': username, 'full_name': full_name},
        )
        return Response(status=204)


class TelegramMyView(APIView):
    """GET /api/integrations/telegram/my?telegram_id= — ближайшие события преподавателя.

    Источник — build_calendar (то же ядро, что кормит календарь teacher SPA):
    плановые занятия + доп.уроки уже слиты и отсортированы, статусы посчитаны.
    Переиспользуем его, а не собираем данные заново из planned_lessons_in_window.
    """

    authentication_classes = [BotServiceAuthentication]
    permission_classes = [IsBotService]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'bot_service'

    WINDOW_DAYS = 7

    def get(self, request: Request) -> Response:
        raw_telegram_id = request.query_params.get('telegram_id')
        if not raw_telegram_id:
            raise ValidationError('telegram_id обязателен.')
        telegram_id = _parse_telegram_id(raw_telegram_id)

        recipient = (
            TelegramRecipient.objects
            .filter(telegram_user__chat_id=telegram_id)
            .select_related('teacher')
            .first()
        )
        if recipient is None:
            raise NotFound('Аккаунт не привязан к преподавателю.')

        today = msk_now().date()
        window_to = today + datetime.timedelta(days=self.WINDOW_DAYS)
        calendar = build_calendar(today, window_to, teacher_id=recipient.teacher_id)

        # Доп.уроки уже в occurrences (build_calendar их подмешивает) — извлекаем
        # только то, что нужно команде /my; extraLessonId != None → доп.урок.
        events = [{
            'date': occ['date'],
            'time': occ['time'],
            'group': occ['group'],
            'direction': occ['direction'],
            'status': occ['label'],
            'is_extra': occ['extraLessonId'] is not None,
        } for occ in calendar['occurrences']]

        return Response({'teacher_name': recipient.teacher.name, 'events': events})
