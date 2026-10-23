"""
Маршруты справочника причин отмены.

Монтируются в config/urls.py как:
  path('api/admin/cancellation-reasons', include('apps.cancellations.urls'))
"""
from django.urls import path

from apps.cancellations.views import CancellationReasonDetailView, CancellationReasonListView

urlpatterns = [
    path('', CancellationReasonListView.as_view(), name='cancellation-reasons-list'),
    path('/<int:pk>', CancellationReasonDetailView.as_view(), name='cancellation-reasons-detail'),
]
