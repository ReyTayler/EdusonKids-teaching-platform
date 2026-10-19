"""
Маршруты admin-API раздела «Уведомления».

Монтируются в config/urls.py как:
  path('api/admin/notifications', include('apps.notifications.urls'))
"""
from django.urls import path

from apps.notifications.views import (
    NotificationListView, NotificationScheduleView, NotificationToggleView,
)

urlpatterns = [
    path('', NotificationListView.as_view(), name='notifications-list'),
    path('/schedule', NotificationScheduleView.as_view(), name='notifications-schedule'),
    path('/toggle', NotificationToggleView.as_view(), name='notifications-toggle'),
]
