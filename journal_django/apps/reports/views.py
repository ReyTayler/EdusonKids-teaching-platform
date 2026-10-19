"""
Вьюхи раздела «Отчёты» (RBAC: manager/admin/superadmin).

  POST /api/admin/reports/<report_type>/run          — поставить генерацию в очередь;
  GET  /api/admin/reports/status/<task_id>           — статус (для поллинга);
  GET  /api/admin/reports/download/<task_id>         — скачать готовый xlsx.

Отчёты НЕ хранятся на платформе: генерация уходит в Celery, готовый файл живёт
только в celery result backend (эфемерно, с TTL) и стримится клиенту по task_id.
В нашу БД ничего не пишется. Паттерн опроса — как в apps.sync (AsyncResult).
"""
from __future__ import annotations

import base64

from celery.result import AsyncResult
from django.http import HttpResponse
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import log_event
from apps.core.permissions import IsManagerOrAdmin
from apps.reports.models import ReportType
from apps.reports.serializers import (
    AccountingReportParamsSerializer,
    AccountingStudentsParamsSerializer,
    AttendanceReportParamsSerializer,
    PaidThroughParamsSerializer,
    RenewalsReportParamsSerializer,
    RetentionReportParamsSerializer,
    RevenueForecastParamsSerializer,
    StudentsByTeacherParamsSerializer,
)
from apps.reports.tasks import generate_report_task

XLSX_CONTENT_TYPE = (
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
)

# Валидатор параметров под каждый тип отчёта.
_PARAM_SERIALIZERS = {
    ReportType.RENEWALS_MONTH: RenewalsReportParamsSerializer,
    ReportType.ACCOUNTING_MONTH: AccountingReportParamsSerializer,
    ReportType.ATTENDANCE_MONTH: AttendanceReportParamsSerializer,
    ReportType.REVENUE_FORECAST: RevenueForecastParamsSerializer,
    ReportType.RETENTION: RetentionReportParamsSerializer,
    ReportType.STUDENTS_BY_TEACHER: StudentsByTeacherParamsSerializer,
    ReportType.ACCOUNTING_STUDENTS: AccountingStudentsParamsSerializer,
    ReportType.PAID_THROUGH: PaidThroughParamsSerializer,
}


class ReportRunView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, report_type: str) -> Response:
        param_ser_cls = _PARAM_SERIALIZERS.get(report_type)
        if param_ser_cls is None:
            raise NotFound({'error': f'Неизвестный тип отчёта: {report_type}'})

        ser = param_ser_cls(data=request.data)
        if not ser.is_valid():
            raise ValidationError(ser.errors)

        async_result = generate_report_task.delay(report_type, dict(ser.validated_data))
        log_event(
            'report.run',
            account_id=getattr(request.user, 'id', None),
            actor_email=getattr(request.user, 'email', None),
            meta={'report_type': report_type, 'params': ser.validated_data,
                  'task_id': async_result.id},
            request=request,
        )
        return Response({'task_id': async_result.id}, status=status.HTTP_202_ACCEPTED)


class ReportStatusView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, task_id: str) -> Response:
        # Celery не различает "неизвестный task_id" и "ещё не начатую задачу" —
        # оба отдают PENDING (ограничение AsyncResult). task_id всегда приходит
        # из предшествующего POST .../run.
        result = AsyncResult(task_id)
        payload = {'state': result.state, 'filename': None, 'row_count': None, 'error': None}
        if result.state == 'SUCCESS':
            data = result.result or {}
            # Сами байты (content_b64) НЕ отдаём в статус — только метаданные.
            payload['filename'] = data.get('filename')
            payload['row_count'] = data.get('row_count')
        elif result.state == 'FAILURE':
            payload['error'] = str(result.result)
        return Response(payload)


class ReportDownloadView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, task_id: str) -> HttpResponse:
        result = AsyncResult(task_id)
        if result.state == 'FAILURE':
            raise ValidationError({'error': str(result.result)})
        if result.state != 'SUCCESS':
            # Ещё не готов (PENDING/STARTED) либо результат истёк в backend'е.
            raise ValidationError({'error': 'Отчёт ещё не готов'})

        data = result.result or {}
        content_b64 = data.get('content_b64')
        if not content_b64:
            raise NotFound({'error': 'Файл отчёта недоступен (истёк срок хранения результата)'})

        resp = HttpResponse(base64.b64decode(content_b64), content_type=XLSX_CONTENT_TYPE)
        filename = data.get('filename') or f'report_{task_id}.xlsx'
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp
