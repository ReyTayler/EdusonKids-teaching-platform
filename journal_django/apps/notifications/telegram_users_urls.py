"""
Маршрут справочника Telegram-аккаунтов, известных боту.

Отдельный модуль (не urls.py), чтобы не заводить namespace ради одного
эндпоинта. Монтируется в config/urls.py как:
  path('api/admin/telegram-users', include('apps.notifications.telegram_users_urls'))
"""
from django.urls import path

from apps.notifications.views import TelegramUsersView

urlpatterns = [
    path('', TelegramUsersView.as_view(), name='telegram-users-list'),
]
