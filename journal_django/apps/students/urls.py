"""
URL маршруты для раздела students.

Монтируются в config/urls.py как:
  path('api/admin/students', include('apps.students.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express/Nest).
"""
from django.urls import path

from apps.students.views import (
    StudentBalanceView,
    StudentCommentDetailView,
    StudentCommentListView,
    StudentDetailView,
    StudentLessonsView,
    StudentListCreateView,
    StudentManagerView,
    StudentRefundView,
    StudentStatsView,
)

urlpatterns = [
    path('', StudentListCreateView.as_view(), name='students-list-create'),
    path('/<int:pk>', StudentDetailView.as_view(), name='students-detail'),
    path('/<int:pk>/manager', StudentManagerView.as_view(), name='students-manager'),
    path('/<int:pk>/stats', StudentStatsView.as_view(), name='students-stats'),
    path('/<int:pk>/balance', StudentBalanceView.as_view(), name='students-balance'),
    path('/<int:pk>/lessons', StudentLessonsView.as_view(), name='students-lessons'),
    path('/<int:pk>/comments', StudentCommentListView.as_view(), name='students-comments'),
    path(
        '/<int:pk>/comments/<int:comment_id>',
        StudentCommentDetailView.as_view(),
        name='students-comment-detail',
    ),
    path('/<int:pk>/refund', StudentRefundView.as_view(), name='students-refund'),
]
