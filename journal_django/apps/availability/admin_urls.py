"""Маршруты графика работы в admin SPA — монтируются на 'api/admin/teachers/<int:teacher_id>/work-schedule'."""
from django.urls import path

from apps.availability import views

urlpatterns = [
    path('', views.AdminRangeView.as_view(), name='admin-schedule-range'),
    path('/template', views.AdminTemplateView.as_view(), name='admin-schedule-template'),
    path('/week', views.AdminWeekView.as_view(), name='admin-schedule-week'),
    path('/overrides/<str:date>', views.AdminOverrideView.as_view(), name='admin-schedule-override'),
]
