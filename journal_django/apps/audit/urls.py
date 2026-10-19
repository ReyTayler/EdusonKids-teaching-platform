"""
URL маршруты для раздела audit.

Монтируются в config/urls.py как:
  path('api/admin/audit-log', include('apps.audit.urls'))

GET-only: только список с пагинацией.
"""
from django.urls import path

from apps.audit.views import AuditLogListView

urlpatterns = [
    path('', AuditLogListView.as_view(), name='audit-log-list'),
]
