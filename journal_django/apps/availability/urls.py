"""Маршруты графика работы в кабинете преподавателя — монтируются на 'api/work-schedule'."""
from django.urls import path

from apps.availability import views

urlpatterns = [
    path('', views.TeacherRangeView.as_view(), name='schedule-range'),
    path('/template', views.TeacherTemplateView.as_view(), name='schedule-template'),
    path('/week', views.TeacherWeekView.as_view(), name='schedule-week'),
    path('/overrides/<str:date>', views.TeacherOverrideView.as_view(), name='schedule-override'),
]
