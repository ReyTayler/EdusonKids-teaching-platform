"""
URL маршруты для раздела payroll.

Монтируются в config/urls.py как:
  path('api/admin/payroll', include('apps.payroll.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express).
Литеральные префиксы (summary, rate-versions, …) — выше /<int:pk>: <int:pk> их
и так не поймает, но порядок держим явным.
"""
from django.urls import path

from apps.payroll import views

urlpatterns = [
    path('', views.PayrollListView.as_view(), name='payroll-list'),
    path('/summary', views.PayrollSummaryView.as_view(), name='payroll-summary'),
    path('/rate-versions', views.RateVersionListView.as_view(),
         name='payroll-rate-versions'),
    path('/rate-versions/<int:pk>', views.RateVersionDetailView.as_view(),
         name='payroll-rate-version-detail'),
    path('/personal-rates', views.PersonalRateListView.as_view(),
         name='payroll-personal-rates'),
    path('/personal-rates/<int:pk>', views.PersonalRateDetailView.as_view(),
         name='payroll-personal-rate-detail'),
    path('/cancellations/<int:pk>', views.CancellationPayDetailView.as_view(),
         name='payroll-cancellation-detail'),
    path('/cancellations/<int:pk>/reset', views.CancellationPayResetView.as_view(),
         name='payroll-cancellation-reset'),
    path('/<int:pk>', views.PayrollDetailView.as_view(), name='payroll-detail'),
    path('/<int:pk>/reset', views.PayrollResetView.as_view(), name='payroll-reset'),
]
