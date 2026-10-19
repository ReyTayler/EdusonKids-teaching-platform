"""
URL маршруты для раздела memberships.

Монтируются в config/urls.py как:
  path('api/admin/memberships', include('apps.memberships.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express/Nest).
"""
from django.urls import path

from apps.memberships.views import (
    MembershipDetailView,
    MembershipListCreateView,
    MembershipTransferView,
)

urlpatterns = [
    path('', MembershipListCreateView.as_view(), name='memberships-list-create'),
    path('/<int:pk>', MembershipDetailView.as_view(), name='memberships-detail'),
    path('/<int:pk>/transfer', MembershipTransferView.as_view(), name='memberships-transfer'),
]
