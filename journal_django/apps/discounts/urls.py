"""
URL маршруты для раздела discounts.

Монтируются в config/urls.py как:
  path('api/admin/discounts', include('apps.discounts.urls'))
"""
from django.urls import path

from apps.discounts.views import DiscountDetailView, DiscountListCreateView

urlpatterns = [
    path('', DiscountListCreateView.as_view(), name='discounts-list-create'),
    path('/<int:pk>', DiscountDetailView.as_view(), name='discounts-detail'),
]
