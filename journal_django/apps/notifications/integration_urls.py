"""Маршруты служебного API бота. Монтируются как /api/integrations/telegram."""
from django.urls import path

from apps.notifications.integration_views import TelegramIdentifyView, TelegramMyView

urlpatterns = [
    path('/identify', TelegramIdentifyView.as_view(), name='telegram-identify'),
    path('/my', TelegramMyView.as_view(), name='telegram-my'),
]
