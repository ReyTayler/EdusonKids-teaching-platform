"""URL-маршруты teacher-раздела extra_lessons. Монтируется как
/api/extra-lessons в config/urls.py (после /api/admin — teacher-guard)."""
from django.urls import path

from apps.extra_lessons.views import (
    TeacherExtraLessonDetailView, TeacherExtraLessonRecordView,
)

urlpatterns = [
    path('/<int:pk>', TeacherExtraLessonDetailView.as_view(), name='teacher-extra-lessons-detail'),
    path('/<int:pk>/record', TeacherExtraLessonRecordView.as_view(), name='teacher-extra-lessons-record'),
]
