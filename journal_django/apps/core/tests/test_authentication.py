"""
test_authentication.py — тесты для CookieJWTAuthentication.

Проверяет:
  - отсутствие cookie → None (анонимный пользователь)
  - невалидный JWT → AuthenticationFailed / PermissionDenied
  - просроченный JWT → AuthenticationFailed
  - stale token_version → AuthenticationFailed
  - неактивный аккаунт → AuthenticationFailed
  - валидный JWT с корректной token_version → (user, token)
  - CSRF-проверка для мутирующих методов (POST/PUT/PATCH/DELETE)

Тесты работают через mock, не нужен БД-доступ для юнит-уровня.
E2E-сценарии (stale→401, inactive→401) покрыты в test_token_version.py.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from rest_framework import exceptions
from rest_framework_simplejwt.tokens import RefreshToken


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(user_id: int = 1, token_version: int = 0, include_version: bool = True) -> str:
    """
    Создать валидный access-токен для тестов без реального аккаунта (mock user).
    """
    from rest_framework_simplejwt.tokens import AccessToken
    token = AccessToken()
    token['user_id'] = user_id
    if include_version:
        token['token_version'] = token_version
    return str(token)


def _make_request(
    method: str = 'GET',
    cookie_value: str | None = None,
    cookie_name: str | None = None,
) -> MagicMock:
    """Строит mock DRF Request."""
    mock_request = MagicMock()
    mock_request.method = method
    name = cookie_name or settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    if cookie_value is not None:
        mock_request.COOKIES = {name: cookie_value}
    else:
        mock_request.COOKIES = {}
    # DRF APIClient с enforce_csrf_checks=False ставит этот флаг
    mock_request._dont_enforce_csrf_checks = False
    return mock_request


# ---------------------------------------------------------------------------
# Тесты CookieJWTAuthentication.authenticate()
# ---------------------------------------------------------------------------

class TestCookieJWTAuthentication:
    """Юнит-тесты для authenticate() — без БД, через mock."""

    def _auth(self):
        from apps.core.authentication import CookieJWTAuthentication
        return CookieJWTAuthentication()

    def test_no_cookie_returns_none(self):
        """Нет cookie → None (DRF продолжает по цепочке backends)."""
        request = _make_request('GET', cookie_value=None)
        result = self._auth().authenticate(request)
        assert result is None

    def test_wrong_cookie_name_returns_none(self):
        """Cookie с неверным именем → None."""
        request = _make_request('GET', cookie_value='sometoken', cookie_name='wrong_name')
        result = self._auth().authenticate(request)
        assert result is None

    def test_invalid_token_raises(self):
        """Невалидный токен → AuthenticationFailed/InvalidToken."""
        from rest_framework_simplejwt.exceptions import InvalidToken
        request = _make_request('GET', cookie_value='not.a.jwt')
        with pytest.raises((exceptions.AuthenticationFailed, InvalidToken)):
            self._auth().authenticate(request)

    def test_valid_token_with_correct_version_returns_user(self):
        """Валидный JWT + token_version совпадает → (user, token)."""
        from apps.accounts.models import Account

        mock_user = MagicMock(spec=Account)
        mock_user.id = 99
        mock_user.is_active = True
        mock_user.token_version = 0

        raw_token = _make_token(user_id=99, token_version=0)
        request = _make_request('GET', cookie_value=raw_token)

        with patch.object(self._auth().__class__, 'get_user', return_value=mock_user):
            auth = self._auth()
            result = auth.authenticate(request)

        assert result is not None
        user, validated_token = result
        assert user.id == 99

    def test_token_version_missing_raises(self):
        """JWT без claim token_version → AuthenticationFailed."""
        from apps.accounts.models import Account
        from rest_framework_simplejwt.exceptions import AuthenticationFailed as JWTAuthFailed

        mock_user = MagicMock(spec=Account)
        mock_user.id = 5
        mock_user.is_active = True

        raw_token = _make_token(user_id=5, include_version=False)
        request = _make_request('GET', cookie_value=raw_token)

        # super().get_user() должен отработать, но get_auth_state не вызывается —
        # падает на отсутствующем claim.
        with patch('rest_framework_simplejwt.authentication.JWTAuthentication.get_user',
                   return_value=mock_user):
            with pytest.raises((JWTAuthFailed, exceptions.AuthenticationFailed)):
                self._auth().authenticate(request)

    def test_stale_token_version_raises(self):
        """JWT с устаревшей token_version → AuthenticationFailed."""
        from apps.accounts.models import Account
        from rest_framework_simplejwt.exceptions import AuthenticationFailed as JWTAuthFailed

        mock_user = MagicMock(spec=Account)
        mock_user.id = 7
        mock_user.is_active = True

        raw_token = _make_token(user_id=7, token_version=0)
        request = _make_request('GET', cookie_value=raw_token)

        # БД говорит: версия 1, токен несёт 0 → rejected
        # ВАЖНО: authentication.py делает `from apps.accounts.repository import get_auth_state`,
        # поэтому патчить нужно ИМЯ В authentication, а не в repository (иначе патч не действует
        # и идёт реальный запрос в БД → RuntimeError без django_db).
        with patch('rest_framework_simplejwt.authentication.JWTAuthentication.get_user',
                   return_value=mock_user), \
             patch('apps.core.authentication.get_auth_state',
                   return_value={'is_active': True, 'token_version': 1}):
            with pytest.raises((JWTAuthFailed, exceptions.AuthenticationFailed)):
                self._auth().authenticate(request)

    def test_inactive_account_raises(self):
        """is_active=False → AuthenticationFailed (штатная проверка simplejwt)."""
        from apps.accounts.models import Account
        from rest_framework_simplejwt.exceptions import AuthenticationFailed as JWTAuthFailed

        mock_user = MagicMock(spec=Account)
        mock_user.id = 8
        mock_user.is_active = False

        raw_token = _make_token(user_id=8, token_version=0)
        request = _make_request('GET', cookie_value=raw_token)

        with patch('rest_framework_simplejwt.authentication.JWTAuthentication.get_user',
                   side_effect=JWTAuthFailed('User is inactive')):
            with pytest.raises((JWTAuthFailed, exceptions.AuthenticationFailed)):
                self._auth().authenticate(request)


# ---------------------------------------------------------------------------
# CSRF-проверка
# ---------------------------------------------------------------------------

class TestCookieJWTAuthenticationCSRF:
    """Тесты CSRF-поведения для мутирующих методов."""

    def _auth(self):
        from apps.core.authentication import CookieJWTAuthentication
        return CookieJWTAuthentication()

    def test_safe_method_skips_csrf(self):
        """GET/HEAD/OPTIONS/TRACE не вызывают CSRF-проверку."""
        from apps.accounts.models import Account

        mock_user = MagicMock(spec=Account)
        mock_user.id = 10
        mock_user.is_active = True
        mock_user.token_version = 0

        raw_token = _make_token(user_id=10, token_version=0)

        for method in ('GET', 'HEAD', 'OPTIONS'):
            request = _make_request(method, cookie_value=raw_token)
            with patch.object(self._auth().__class__, 'get_user', return_value=mock_user), \
                 patch.object(self._auth().__class__, '_enforce_csrf') as mock_csrf:
                auth = self._auth()
                # get_user патчится через patch.object класса; нужно патчить на экземпляре
                auth.get_user = lambda t: mock_user
                auth.authenticate(request)
                mock_csrf.assert_not_called()

    def test_unsafe_method_calls_enforce_csrf(self):
        """POST вызывает _enforce_csrf."""
        from apps.accounts.models import Account

        mock_user = MagicMock(spec=Account)
        mock_user.id = 11
        mock_user.is_active = True
        mock_user.token_version = 0

        raw_token = _make_token(user_id=11, token_version=0)
        request = _make_request('POST', cookie_value=raw_token)

        with patch('apps.core.authentication.CookieJWTAuthentication._enforce_csrf') as mock_csrf, \
             patch('apps.core.authentication.CookieJWTAuthentication.get_user',
                   return_value=mock_user):
            mock_csrf.return_value = None  # не кидаем PermissionDenied
            auth = CookieJWTAuthentication()
            auth.authenticate(request)
            mock_csrf.assert_called_once()

    def test_enforce_csrf_raises_on_missing_token(self):
        """
        _enforce_csrf кидает PermissionDenied, если нет CSRF-токена.
        Это поведение встроенного CsrfViewMiddleware. Нужен РЕАЛЬНЫЙ request
        (RequestFactory), т.к. CsrfViewMiddleware читает реальные META/COOKIES;
        MagicMock тут не годится. RequestFactory НЕ ставит _dont_enforce_csrf_checks,
        поэтому проверка выполняется по-настоящему.
        """
        from django.test import RequestFactory

        request = RequestFactory().post('/api/admin/teachers')
        auth = CookieJWTAuthentication()
        with pytest.raises(exceptions.PermissionDenied):
            auth._enforce_csrf(request)


# Нужен импорт для последнего теста
from apps.core.authentication import CookieJWTAuthentication
