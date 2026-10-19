"""
URL-конфиг admin-календаря — /api/admin/calendar (role=manager/admin/
superadmin). Отдельный файл от urls.py (тот монтируется под /api,
teacher-guard секция): этот монтируется под /api/admin/*, ДО teacher-guard
(см. config/urls.py, правило «Admin обязан стоять ДО teacher-guard»).
"""
from django.urls import path

from apps.scheduling import views

urlpatterns = [
    path('', views.AdminCalendarView.as_view(), name='scheduling-admin-calendar'),
]
