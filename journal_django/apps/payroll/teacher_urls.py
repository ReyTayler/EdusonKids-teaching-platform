"""URL-маршруты teacher-раздела payroll. Монтируется как /api/my-payroll в
config/urls.py (после /api/admin — teacher-guard).

Отдельный модуль, а не urls.py: у admin-раздела payroll своя точка монтирования
(/api/admin/payroll) и своё право доступа (IsSuperAdmin). Смешивать их в одном
urlconf нельзя — префикс определяет, кто читает данные.
"""
from django.urls import path

from apps.payroll.views import MyPayrollView

urlpatterns = [
    path('', MyPayrollView.as_view(), name='teacher-my-payroll'),
]
