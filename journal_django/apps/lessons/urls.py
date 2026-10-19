"""
URL маршруты для раздела lessons.

Монтируются в config/urls.py как:
  path('api/admin/lessons', include('apps.lessons.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express).
"""
from django.urls import path

from apps.lessons.views import (
    AttendanceCellView, AttendanceUnpaidSkipView, LessonDetailView, LessonListCreateView,
)

urlpatterns = [
    path('', LessonListCreateView.as_view(), name='lessons-list-create'),
    path('/<int:pk>', LessonDetailView.as_view(), name='lessons-detail'),
    path(
        '/<int:lesson_id>/attendance/<int:student_id>',
        AttendanceCellView.as_view(),
        name='lessons-attendance-cell',
    ),
    path(
        '/<int:lesson_id>/unpaid-skip/<int:student_id>',
        AttendanceUnpaidSkipView.as_view(),
        name='lessons-unpaid-skip',
    ),
]
