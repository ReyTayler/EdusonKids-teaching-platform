"""
Аутентификация служебных эндпоинтов бота.

Общий секрет в заголовке X-Bot-Token, сравнение за постоянное время. Это НЕ
пользовательская аутентификация: request.user остаётся анонимным, доступ решает
permission-класс.

Аутентификация не сессионная ⇒ CSRF к этим вьюхам не применяется; @csrf_exempt
не нужен и не ставится.
"""
from __future__ import annotations

from django.conf import settings
from django.utils.crypto import constant_time_compare
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission

HEADER = 'HTTP_X_BOT_TOKEN'


def _token_matches(request) -> bool:
    expected = getattr(settings, 'BOT_SERVICE_TOKEN', '')
    if not expected:
        # Пустой секрет в настройках не означает «пускать всех».
        return False
    provided = request.META.get(HEADER, '')
    return bool(provided) and constant_time_compare(provided, expected)


class BotServiceAuthentication(BaseAuthentication):
    """Ничего не аутентифицирует — нужна только чтобы DRF не вернул 403 из-за CSRF."""

    def authenticate(self, request):
        return None


class IsBotService(BasePermission):
    """Пускает только запросы с корректным X-Bot-Token."""
    message = 'Bot service token required.'

    def has_permission(self, request, view) -> bool:
        return _token_matches(request)
