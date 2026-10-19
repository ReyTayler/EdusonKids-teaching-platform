"""
URL-маршруты «Реестра куратора».

Монтируются в config/urls.py как:
  path('api/admin/registry', include('apps.dashboard.registry_urls'))

APPEND_SLASH=False — пути без trailing slash. Литеральные /summary и /students —
точные совпадения, между собой не конфликтуют.
"""
from django.urls import path

from apps.dashboard.registry_views import RegistryStudentsView, RegistrySummaryView

urlpatterns = [
    path('/summary', RegistrySummaryView.as_view(), name='registry-summary'),
    path('/students', RegistryStudentsView.as_view(), name='registry-students'),
]
