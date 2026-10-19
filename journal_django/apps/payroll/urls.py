"""
URL маршруты для раздела payroll.

Монтируются в config/urls.py как:
  path('api/admin/payroll', include('apps.payroll.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express).
"""
from django.urls import path

from apps.payroll.views import PayrollDetailView, PayrollListView, PayrollSummaryView

urlpatterns = [
    path('', PayrollListView.as_view(), name='payroll-list'),
    path('/summary', PayrollSummaryView.as_view(), name='payroll-summary'),
    path('/<int:pk>', PayrollDetailView.as_view(), name='payroll-detail'),
]
