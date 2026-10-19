"""
URL-конфиг scheduling. Монтируется под /api (после /api/admin — teacher-guard).
APPEND_SLASH=False — без trailing slash (как остальные teacher-эндпоинты).
"""
from django.urls import path

from apps.scheduling import views

urlpatterns = [
    path('/calendar', views.CalendarView.as_view(), name='scheduling-calendar'),
]
