"""
views.py — auth эндпоинты на стандартных DRF-классах.

Аутентификация: JWT через HttpOnly-cookie (CookieJWTAuthentication).
Rate-limit: django-ratelimit.
Валидация: DRF serializers.
"""
from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django_ratelimit.decorators import ratelimit

from rest_framework import status
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenRefreshView as BaseTokenRefreshView

from apps.accounts import repository as accounts_repo
from apps.audit.services import log_event
from apps.auth_app import services
from apps.auth_app.serializers import (
    EmailSendSerializer,
    InviteAcceptSerializer,
    Login2faSerializer,
    LoginSerializer,
    MeSerializer,
    TwofaDisableSerializer,
    TwofaEnableSerializer,
    TwofaSetupSerializer,
)
from apps.core.authentication import (
    delete_auth_cookies,
    issue_tokens_for,
    set_auth_cookies,
)


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@method_decorator(ratelimit(key='ip', rate='5/15m', method='POST', block=True), name='post')
class LoginView(APIView):
    """Первый шаг входа."""
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        d = serializer.validated_data
        data, http_status, user = services.login(
            email=d['email'],
            password=d['password'],
            role=d['role'],
            request=request,
        )
        response = Response(data, status=http_status)
        if user is not None:
            refresh = issue_tokens_for(user)
            set_auth_cookies(response, refresh)
        return response


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------

class LogoutView(APIView):
    """Выход — удаляет JWT-cookie и отзывает все выданные токены."""
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        # Бамп token_version отзывает и access, и 7-дневный refresh на сервере:
        # даже перехваченный refresh после логаута станет невалидным
        # (CookieJWTAuthentication.get_user сверяет версию на каждом запросе).
        accounts_repo.bump_token_version(request.user.id)
        # Событие ИБ: парная запись к login_success. Без неё в журнале виден вход,
        # но не выход, и отзыв всех токенов (bump выше) остаётся неотражённым.
        log_event(
            'logout',
            account_id=request.user.id,
            actor_email=getattr(request.user, 'email', None),
            request=request,
        )
        response = Response({'ok': True})
        delete_auth_cookies(response)
        return response


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------

class MeView(RetrieveAPIView):
    """Текущий аккаунт."""
    permission_classes = [IsAuthenticated]
    serializer_class = MeSerializer

    def get_object(self):
        # self.request.user (CookieJWTAuthentication) не несёт teacher_name —
        # без annotate MeSerializer.get_name() для teacher-учёток отдавал бы email.
        from django.db.models import F

        from apps.accounts.models import Account

        return Account.objects.annotate(
            teacher_name=F('teacher__name'),
        ).get(pk=self.request.user.pk)


# ---------------------------------------------------------------------------
# GET /csrf
# ---------------------------------------------------------------------------

@method_decorator(ensure_csrf_cookie, name='dispatch')
class CsrfView(APIView):
    """
    GET /api/auth/csrf — выставляет csrftoken-cookie для SPA.

    SPA дёргает этот endpoint при старте, читает csrftoken-cookie
    (CSRF_COOKIE_HTTPONLY=False) и шлёт его как заголовок X-CSRFToken
    на всех мутирующих запросах. Без этого CookieJWTAuthentication
    отклонит мутации авторизованного SPA с 403 (CSRF Failed).

    AllowAny: cookie нужна ещё до завершения входа; ensure_csrf_cookie
    форсирует CsrfViewMiddleware выставить cookie в ответе.
    """
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /login/2fa
# ---------------------------------------------------------------------------

@method_decorator(ratelimit(key='ip', rate='10/15m', method='POST', block=True), name='post')
class Login2faView(APIView):
    """Завершение входа по 2FA-коду."""
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = Login2faSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        d = serializer.validated_data
        data, http_status, user = services.login_2fa(
            challenge_token=d['challenge_token'],
            code=d['code'],
            request=request,
        )
        response = Response(data, status=http_status)
        if user is not None:
            refresh = issue_tokens_for(user)
            set_auth_cookies(response, refresh)
        return response


# ---------------------------------------------------------------------------
# POST /2fa/email/send
# ---------------------------------------------------------------------------

@method_decorator(ratelimit(key='ip', rate='3/h', method='POST', block=True), name='post')
class Email2faSendView(APIView):
    """Повторно отправить email-OTP."""
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = EmailSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data, http_status = services.email_send(
            challenge_token=serializer.validated_data['challenge_token'],
            request=request,
        )
        return Response(data, status=http_status)


# ---------------------------------------------------------------------------
# POST /2fa/setup
# ---------------------------------------------------------------------------

class TwofaSetupView(APIView):
    """Enrollment: настройка метода 2FA."""
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = TwofaSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        d = serializer.validated_data
        data, http_status = services.twofa_setup(
            challenge_token=d.get('challenge_token'),
            method=d['method'],
            request=request,
        )
        return Response(data, status=http_status)


# ---------------------------------------------------------------------------
# POST /2fa/enable
# ---------------------------------------------------------------------------

class TwofaEnableView(APIView):
    """Enrollment: подтвердить код, включить 2FA."""
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = TwofaEnableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        d = serializer.validated_data
        data, http_status, user = services.twofa_enable(
            challenge_token=d.get('challenge_token'),
            code=d['code'],
            request=request,
        )
        response = Response(data, status=http_status)
        if user is not None:
            refresh = issue_tokens_for(user)
            set_auth_cookies(response, refresh)
        return response


# ---------------------------------------------------------------------------
# POST /2fa/disable
# ---------------------------------------------------------------------------

class TwofaDisableView(APIView):
    """Выключить 2FA."""
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        serializer = TwofaDisableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data, http_status = services.twofa_disable(
            account_id=request.user.id,
            password=serializer.validated_data['password'],
            request=request,
        )
        return Response(data, status=http_status)


# ---------------------------------------------------------------------------
# GET /invite
# ---------------------------------------------------------------------------

@method_decorator(ratelimit(key='ip', rate='10/15m', method='GET', block=True), name='get')
class InviteLookupView(APIView):
    """Проверить invite-токен."""
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        token = request.query_params.get('token', '')
        data, http_status = services.invite_lookup(token)
        return Response(data, status=http_status)


# ---------------------------------------------------------------------------
# POST /invite/accept
# ---------------------------------------------------------------------------

@method_decorator(ratelimit(key='ip', rate='10/15m', method='POST', block=True), name='post')
class InviteAcceptView(APIView):
    """Принять invite: установить пароль."""
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = InviteAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        d = serializer.validated_data
        data, http_status, user = services.invite_accept(
            token=d['token'],
            password=d['password'],
            request=request,
        )
        return Response(data, status=http_status)


# ---------------------------------------------------------------------------
# POST /refresh
# ---------------------------------------------------------------------------

class RefreshView(BaseTokenRefreshView):
    """
    Обновление access-токена из refresh-cookie.

    Стандартный TokenRefreshView ожидает refresh в теле запроса.
    Переопределяем post(): берём refresh из cookie, валидируем через
    TokenRefreshSerializer, выставляем новый access-cookie.

    token_version копируется в новый access автоматически — simplejwt
    копирует все кастомные claims из refresh в access (не входят в no_copy_claims).
    """
    # Точка входа по credentials/challenge — НЕ зависит от JWT-cookie.
    # Снимаем глобальный CookieJWTAuthentication: иначе остаточная access-cookie
    # навязала бы CSRF-проверку на анонимный вход (POST /login со старой cookie → 403).
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request, *args, **kwargs) -> Response:
        from django.conf import settings as django_settings

        refresh_cookie = django_settings.SIMPLE_JWT.get('AUTH_REFRESH_COOKIE', 'refresh')
        raw_refresh = request.COOKIES.get(refresh_cookie)

        if not raw_refresh:
            return Response(
                {'error': 'Refresh-токен отсутствует. Выполните вход заново.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = self.get_serializer(data={'refresh': raw_refresh})

        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])

        # serializer.validated_data содержит {'access': '<новый access JWT>'}
        new_access = serializer.validated_data['access']

        response = Response({'ok': True}, status=status.HTTP_200_OK)

        jwt_settings = django_settings.SIMPLE_JWT
        access_cookie = jwt_settings.get('AUTH_COOKIE', 'access')
        access_max_age = int(jwt_settings['ACCESS_TOKEN_LIFETIME'].total_seconds())

        response.set_cookie(
            key=access_cookie,
            value=new_access,
            max_age=access_max_age,
            httponly=jwt_settings.get('AUTH_COOKIE_HTTPONLY', True),
            samesite=jwt_settings.get('AUTH_COOKIE_SAMESITE', 'Lax'),
            secure=jwt_settings.get('AUTH_COOKIE_SECURE', False),
            path='/',
        )
        return response
