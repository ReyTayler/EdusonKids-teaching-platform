"""
URL маршруты журнала изменений.

Монтируются в config/urls.py как:
  path('api/admin/changelog', include('apps.changelog.urls'))
"""
from django.urls import path

from apps.changelog.views import (
    ChangelogDetailView, ChangelogListView, ChangelogRevertView,
)

urlpatterns = [
    path('', ChangelogListView.as_view(), name='changelog-list'),
    path('/<uuid:context_id>', ChangelogDetailView.as_view(), name='changelog-detail'),
    path('/<uuid:context_id>/revert', ChangelogRevertView.as_view(),
         name='changelog-revert'),
]
