"""
URL маршруты для раздела payments.

Монтируются в config/urls.py как:
  path('api/admin/payments', include('apps.payments.urls'))
"""
from django.urls import path

from apps.payments.views import PaymentDetailView, PaymentListCreateView

urlpatterns = [
    path('', PaymentListCreateView.as_view(), name='payments-list-create'),
    path('/<int:pk>', PaymentDetailView.as_view(), name='payments-detail'),
]
