"""Маршруты renewals. APPEND_SLASH=False — без trailing slash."""
from django.urls import path

from apps.renewals.views import (
    RenewalActivityView,
    RenewalAnalyticsView,
    RenewalAssigneesView,
    RenewalCollectionView,
    RenewalColumnCardsView,
    RenewalCommentView,
    RenewalDetailView,
    RenewalMoveView,
    RenewalOutcomeDateView,
    RenewalReopenView,
    RenewalStageDetailView,
    RenewalStageListView,
    RenewalStageReorderView,
    RenewalUnassignedCountView,
    RenewalUnassignedView,
    RenewalUnfreezeView,
)

urlpatterns = [
    path('', RenewalCollectionView.as_view(), name='renewals-collection'),
    # Литеральные /stages*, /analytics, /columns/*, /assignees — до /<int:pk>,
    # чтобы не было двусмысленности путей.
    path('/stages', RenewalStageListView.as_view(), name='renewals-stages'),
    path('/stages/reorder', RenewalStageReorderView.as_view(), name='renewals-stages-reorder'),
    path('/stages/<int:pk>', RenewalStageDetailView.as_view(), name='renewals-stage-detail'),
    path('/analytics', RenewalAnalyticsView.as_view(), name='renewals-analytics'),
    path('/assignees', RenewalAssigneesView.as_view(), name='renewals-assignees'),
    path('/unassigned', RenewalUnassignedView.as_view(), name='renewals-unassigned'),
    path('/unassigned/count', RenewalUnassignedCountView.as_view(),
         name='renewals-unassigned-count'),
    path('/columns/<int:stage_id>', RenewalColumnCardsView.as_view(), name='renewals-column-cards'),
    path('/<int:pk>', RenewalDetailView.as_view(), name='renewals-detail'),
    path('/<int:pk>/move', RenewalMoveView.as_view(), name='renewals-move'),
    path('/<int:pk>/reopen', RenewalReopenView.as_view(), name='renewals-reopen'),
    path('/<int:pk>/unfreeze', RenewalUnfreezeView.as_view(), name='renewals-unfreeze'),
    path('/<int:pk>/outcome-date', RenewalOutcomeDateView.as_view(),
         name='renewals-outcome-date'),
    path('/<int:pk>/comment', RenewalCommentView.as_view(), name='renewals-comment'),
    path('/<int:pk>/activity', RenewalActivityView.as_view(), name='renewals-activity'),
]
