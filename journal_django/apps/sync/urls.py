"""Маршруты sync. APPEND_SLASH=False — без trailing slash."""
from django.urls import path

from apps.sync.views import SyncRunView, SyncStatusView

urlpatterns = [
    path('/status/<str:task_id>', SyncStatusView.as_view(), name='sync-status'),
    path('/<str:action>/run', SyncRunView.as_view(), name='sync-run'),
]
