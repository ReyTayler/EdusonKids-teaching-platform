"""
URL маршруты для раздела teachers.

Монтируются в config/urls.py как:
  path('api/admin/teachers', include('apps.teachers.urls'))
"""
from django.urls import path

from apps.notifications.views import TeacherTelegramView
from apps.teachers.views import TeacherDetailView, TeacherListCreateView, TeacherStatsView

urlpatterns = [
    path('', TeacherListCreateView.as_view(), name='teachers-list-create'),
    path('/<int:pk>', TeacherDetailView.as_view(), name='teachers-detail'),
    path('/<int:pk>/stats', TeacherStatsView.as_view(), name='teachers-stats'),
    path('/<int:teacher_id>/telegram', TeacherTelegramView.as_view(),
         name='teacher-telegram'),
]
