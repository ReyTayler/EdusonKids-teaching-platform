"""
URL маршруты для раздела dashboard.

Монтируются в config/urls.py как:
  path('api/admin/dashboard', include('apps.dashboard.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express).
Литеральные пути (/revenue, /unfilled-lessons) не конфликтуют с корнем ''.
"""
from django.urls import path

from apps.dashboard.fill_views import UnfilledLessonsView
from apps.dashboard.views import DashboardRevenueView, DashboardView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('/revenue', DashboardRevenueView.as_view(), name='dashboard-revenue'),
    path('/unfilled-lessons', UnfilledLessonsView.as_view(), name='dashboard-unfilled-lessons'),
]
