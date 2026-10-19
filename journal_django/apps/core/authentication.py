"""
apps/core/authentication.py — JWT-аутентификация через HttpOnly-cookie.

Паттерн: тонкий подкласс JWTAuthentication (djangorestframework-simplejwt).
Единственная кастомная бизнес-логика — проверка token_version для мгновенного
отзыва всех токенов без blacklist (см. architecture_v2.md).

Хелперы set_auth_cookies / delete_auth_cookies / issue_tokens_for используются
в apps/auth_app/views.py (выдача и сброс токенов при входе/выходе).
"""
from __future__ import annotations

import pghistory
from django.conf import settings
from rest_framework.authentication import CSRFCheck
from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.repository import get_auth_state


# ---------------------------------------------------------------------------
# Небезопасные методы, требующие CSRF-проверки
# ---------------------------------------------------------------------------
_UNSAFE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


class CookieJWTAuthentication(JWTAuthentication):
    """
    Читает JWT из HttpOnly-cookie вместо заголовка Authorization.

    Для небезопасных методов выполняет CSRF-проверку (зеркало
    SessionAuthentication.enforce_csrf из DRF).

    Override get_user дополнительно сверяет claim token_version с БД —
    единственный механизм мгновенного отзыва всех токенов.
    """

    def authenticate(self, request):
        cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
        raw_token = request.COOKIES.get(cookie_name)
        if raw_token is None:
            # Токена нет — пробрасываем None, DRF попробует следующий backend.
            return None

        validated_token = self.get_validated_token(raw_token)

        # CSRF-проверка для мутирующих методов (POST/PUT/PATCH/DELETE).
        # Паттерн из SessionAuthentication.enforce_csrf (DRF 3.15).
        if request.method in _UNSAFE_METHODS:
            self._enforce_csrf(request)

        user = self.get_user(validated_token)

        # Журнал изменений: дописать актора в pghistory-контекст запроса.
        # Функция-вызов (не контекст-менеджер) добавляет metadata ТОЛЬКО если
        # middleware уже открыл сессию (мутирующие методы) — на GET это no-op.
        # name — как в /api/auth/me: имя преподавателя или email.
        actor_name = user.get_full_name() or None
        if user.teacher_id and not actor_name:
            teacher = getattr(user, 'teacher', None)
            actor_name = teacher.name if teacher else None
        pghistory.context(
            account_id=user.id, email=user.email, role=user.role,
            name=actor_name or user.email,
        )

        return user, validated_token

    def _enforce_csrf(self, request) -> None:
        """
        Зеркало SessionAuthentication.enforce_csrf:
        создаём CSRFCheck, прогоняем запрос, при ошибке — PermissionDenied.
        """
        def _dummy_get_response(req):  # pragma: no cover
            return None

        check = CSRFCheck(_dummy_get_response)
        # process_request заполняет request.META['CSRF_COOKIE']
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied('CSRF Failed: %s' % reason)

    def get_user(self, validated_token):
        """
        Загружает пользователя через super() (проверяет is_active),
        затем сверяет token_version из claim с актуальным значением в БД.
        """
        user = super().get_user(validated_token)

        # Проверка token_version — единственный механизм отзыва.
        try:
            token_version_claim = validated_token['token_version']
        except KeyError:
            raise AuthenticationFailed(
                'Токен не содержит token_version. Выполните вход заново.',
                code='token_version_missing',
            )

        auth_state = get_auth_state(user.id)
        if auth_state is None or auth_state['token_version'] != token_version_claim:
            raise AuthenticationFailed(
                'Токен устарел. Выполните вход заново.',
                code='token_version_mismatch',
            )

        return user


# ---------------------------------------------------------------------------
# Хелперы выдачи токенов
# ---------------------------------------------------------------------------

def issue_tokens_for(user) -> RefreshToken:
    """
    Создаёт пару refresh/access для пользователя и добавляет claim token_version.
    Claim копируется в access автоматически (не входит в no_copy_claims).

    Использовать в views вместо auth_login():
        refresh = issue_tokens_for(user)
        set_auth_cookies(response, refresh)
    """
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version
    return refresh


def set_auth_cookies(response, refresh: RefreshToken) -> None:
    """
    Ставит access-cookie и refresh-cookie в HttpOnly-режиме.
    Параметры берутся из SIMPLE_JWT settings для консистентности.
    """
    jwt_settings = settings.SIMPLE_JWT

    access_cookie = jwt_settings.get('AUTH_COOKIE', 'access')
    refresh_cookie = jwt_settings.get('AUTH_REFRESH_COOKIE', 'refresh')
    refresh_path = jwt_settings.get('AUTH_REFRESH_COOKIE_PATH', '/')
    httponly = jwt_settings.get('AUTH_COOKIE_HTTPONLY', True)
    samesite = jwt_settings.get('AUTH_COOKIE_SAMESITE', 'Lax')
    secure = jwt_settings.get('AUTH_COOKIE_SECURE', False)

    # max_age вычисляем из lifetime (timedelta → секунды).
    access_max_age = int(jwt_settings['ACCESS_TOKEN_LIFETIME'].total_seconds())
    refresh_max_age = int(jwt_settings['REFRESH_TOKEN_LIFETIME'].total_seconds())

    response.set_cookie(
        key=access_cookie,
        value=str(refresh.access_token),
        max_age=access_max_age,
        httponly=httponly,
        samesite=samesite,
        secure=secure,
        path='/',
    )
    response.set_cookie(
        key=refresh_cookie,
        value=str(refresh),
        max_age=refresh_max_age,
        httponly=httponly,
        samesite=samesite,
        secure=secure,
        path=refresh_path,
    )


def delete_auth_cookies(response) -> None:
    """
    Удаляет access-cookie и refresh-cookie (logout).
    Передаём те же параметры path/samesite/secure, что и при выдаче,
    чтобы браузер нашёл и удалил нужные cookie.
    """
    jwt_settings = settings.SIMPLE_JWT

    access_cookie = jwt_settings.get('AUTH_COOKIE', 'access')
    refresh_cookie = jwt_settings.get('AUTH_REFRESH_COOKIE', 'refresh')
    refresh_path = jwt_settings.get('AUTH_REFRESH_COOKIE_PATH', '/')
    samesite = jwt_settings.get('AUTH_COOKIE_SAMESITE', 'Lax')

    response.delete_cookie(
        key=access_cookie,
        path='/',
        samesite=samesite,
    )
    response.delete_cookie(
        key=refresh_cookie,
        path=refresh_path,
        samesite=samesite,
    )
