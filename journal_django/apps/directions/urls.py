"""
URL маршруты для раздела directions.

Монтируются в config/urls.py как:
  path('api/admin/directions', include('apps.directions.urls'))
"""
from django.urls import path

from apps.directions.views import DirectionDetailView, DirectionListCreateView

urlpatterns = [
    path('', DirectionListCreateView.as_view(), name='directions-list-create'),
    path('/<int:pk>', DirectionDetailView.as_view(), name='directions-detail'),
]
