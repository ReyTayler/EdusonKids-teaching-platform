"""Маршруты taskboard. APPEND_SLASH=False — без trailing slash."""
from django.urls import path

from apps.taskboard.views import (
    AssigneeListView,
    BoardColumnsView,
    BoardCollectionView,
    BoardDetailView,
    ColumnCardsView,
    StageCollectionView,
    StageDetailView,
    StageReorderView,
    TaskActivityView,
    TaskCollectionView,
    TaskCommentView,
    TaskCompleteView,
    TaskDetailView,
    TaskMoveView,
    WeekView,
)

urlpatterns = [
    path('', TaskCollectionView.as_view(), name='tasks-collection'),
    # Литеральные пути ОБЯЗАНЫ стоять ВЫШЕ /<int:pk>, иначе разберутся как pk.
    path('/week', WeekView.as_view(), name='tasks-week'),
    path('/columns/<int:stage_id>', ColumnCardsView.as_view(), name='tasks-column-cards'),
    path('/stages/<int:pk>', StageDetailView.as_view(), name='tasks-stage-detail'),
    # Перестановка — под воронкой, как и создание стадии: раньше board_id
    # выводился из присланных стадий, и пустой набор давал 500.
    path('/boards/<int:board_id>/stages/reorder', StageReorderView.as_view(),
         name='tasks-stages-reorder'),
    path('/boards/<int:board_id>/stages', StageCollectionView.as_view(), name='tasks-stages'),
    path('/boards/<int:board_id>/columns', BoardColumnsView.as_view(),
         name='tasks-board-columns'),
    path('/boards', BoardCollectionView.as_view(), name='tasks-boards'),
    path('/boards/<int:pk>', BoardDetailView.as_view(), name='tasks-board-detail'),
    path('/assignees', AssigneeListView.as_view(), name='tasks-assignees'),
    path('/<int:pk>', TaskDetailView.as_view(), name='tasks-detail'),
    path('/<int:pk>/move', TaskMoveView.as_view(), name='tasks-move'),
    path('/<int:pk>/complete', TaskCompleteView.as_view(), name='tasks-complete'),
    path('/<int:pk>/comment', TaskCommentView.as_view(), name='tasks-comment'),
    path('/<int:pk>/activity', TaskActivityView.as_view(), name='tasks-activity'),
]
