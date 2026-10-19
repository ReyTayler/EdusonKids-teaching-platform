"""Маршруты раздела «Отчёты». APPEND_SLASH=False — без trailing slash."""
from django.urls import path

from apps.reports.dashboard_views import (
    AttendanceDashboardStudentsView,
    AttendanceDashboardSummaryView,
)
from apps.reports.views import (
    ReportDownloadView,
    ReportRunView,
    ReportStatusView,
)

urlpatterns = [
    # Литеральные /status|/download|/attendance-dashboard — до /<report_type>/run
    # (str-конвертер жадный).
    path('/status/<str:task_id>', ReportStatusView.as_view(), name='reports-status'),
    path('/download/<str:task_id>', ReportDownloadView.as_view(), name='reports-download'),
    path('/attendance-dashboard/summary', AttendanceDashboardSummaryView.as_view(),
         name='reports-attendance-dashboard-summary'),
    path('/attendance-dashboard/students', AttendanceDashboardStudentsView.as_view(),
         name='reports-attendance-dashboard-students'),
    path('/<str:report_type>/run', ReportRunView.as_view(), name='reports-run'),
]
