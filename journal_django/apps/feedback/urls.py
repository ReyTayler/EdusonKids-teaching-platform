"""URL-маршруты отзывов по урокам. Монтируется как /api/admin/lesson-feedback
в config/urls.py. APPEND_SLASH=False."""
from django.urls import path

from apps.feedback.views import LessonFeedbackListView

urlpatterns = [
    path('', LessonFeedbackListView.as_view(), name='lesson-feedback-list'),
]
