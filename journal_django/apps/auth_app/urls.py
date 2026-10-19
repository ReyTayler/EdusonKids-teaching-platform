"""
urls.py — маршруты /api/auth/*. Без trailing slash (APPEND_SLASH=False).

Порядок дословно из routes/auth.js: login → logout → me → login/2fa →
2fa/email/send → 2fa/setup → 2fa/enable → 2fa/disable.
"""
from __future__ import annotations

from django.urls import path

from apps.auth_app.views import (
    CsrfView,
    Email2faSendView,
    InviteAcceptView,
    InviteLookupView,
    Login2faView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
    TwofaDisableView,
    TwofaEnableView,
    TwofaSetupView,
)

urlpatterns = [
    path('/login', LoginView.as_view(), name='auth-login'),
    path('/logout', LogoutView.as_view(), name='auth-logout'),
    path('/me', MeView.as_view(), name='auth-me'),
    path('/csrf', CsrfView.as_view(), name='auth-csrf'),
    path('/refresh', RefreshView.as_view(), name='auth-refresh'),
    path('/invite', InviteLookupView.as_view(), name='auth-invite-lookup'),
    path('/invite/accept', InviteAcceptView.as_view(), name='auth-invite-accept'),
    path('/login/2fa', Login2faView.as_view(), name='auth-login-2fa'),
    path('/2fa/email/send', Email2faSendView.as_view(), name='auth-2fa-email-send'),
    path('/2fa/setup', TwofaSetupView.as_view(), name='auth-2fa-setup'),
    path('/2fa/enable', TwofaEnableView.as_view(), name='auth-2fa-enable'),
    path('/2fa/disable', TwofaDisableView.as_view(), name='auth-2fa-disable'),
]
