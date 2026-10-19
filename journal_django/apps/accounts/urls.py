"""
URL маршруты для раздела accounts (роль admin).

Монтируются в config/urls.py как:
  path('api/admin/accounts', include('apps.accounts.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express).
Литеральные суффиксы (/reset-password, /reset-2fa, /invite/revoke, /invite)
не конфликтуют с /<int:pk>.

Порядок /<int:pk>/invite/revoke ДО /<int:pk>/invite — длинный литерал раньше.
"""
from django.urls import path

from apps.accounts.views import (
    AccountDetailView,
    AccountInviteRevokeView,
    AccountInviteView,
    AccountListCreateView,
    AccountReset2faView,
    AccountResetPasswordView,
    AccountSetActiveView,
)

urlpatterns = [
    path('', AccountListCreateView.as_view(), name='accounts-list-create'),
    path('/<int:pk>', AccountDetailView.as_view(), name='accounts-detail'),
    path('/<int:pk>/reset-password', AccountResetPasswordView.as_view(), name='accounts-reset-password'),
    path('/<int:pk>/reset-2fa', AccountReset2faView.as_view(), name='accounts-reset-2fa'),
    # invite/revoke ПЕРЕД invite — длинный литерал раньше (иначе Django не добирается до /revoke)
    path('/<int:pk>/invite/revoke', AccountInviteRevokeView.as_view(), name='accounts-invite-revoke'),
    path('/<int:pk>/invite', AccountInviteView.as_view(), name='accounts-invite'),
    path('/<int:pk>/set-active', AccountSetActiveView.as_view(), name='accounts-set-active'),
]
