"""Маршруты графика работы по всей школе (admin SPA) — монтируются на 'api/admin/work-schedule'."""
from django.urls import path

from apps.availability import views

urlpatterns = [
    path('/free-windows', views.AdminFreeWindowsView.as_view(), name='admin-schedule-free-windows'),
]
