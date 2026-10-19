# journal_django/apps/sync/views.py
"""SyncRunView/SyncStatusView — триггер и опрос статуса sync-задач (только IsSuperAdmin)."""
from __future__ import annotations

from celery.result import AsyncResult
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import log_event
from apps.core.permissions import IsSuperAdmin
from apps.sync import tasks

ACTIONS = {
    'rebuild-payroll': tasks.rebuild_payroll_task,
    'rebuild-counters': tasks.rebuild_counters_task,
    'rebuild-planned-lessons': tasks.rebuild_planned_lessons_task,
    'rebuild-absence-resolutions': tasks.rebuild_absence_resolutions_task,
    'rebuild-renewals': tasks.rebuild_renewals_task,
    'rebuild-renewal-dates': tasks.rebuild_renewal_dates_task,
    'check-plan-health': tasks.check_plan_health_task,
}


class SyncRunView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, action: str) -> Response:
        task_fn = ACTIONS.get(action)
        if task_fn is None:
            raise NotFound({'error': f'Unknown sync action: {action}'})

        dry_run = serializers.BooleanField().to_internal_value(request.data.get('dry_run', False))
        async_result = task_fn.delay(dry_run=dry_run)

        log_event(
            'sync.run',
            account_id=getattr(request.user, 'id', None),
            actor_email=getattr(request.user, 'email', None),
            meta={'action': action, 'dry_run': dry_run, 'task_id': async_result.id},
            request=request,
        )
        return Response({'task_id': async_result.id}, status=status.HTTP_202_ACCEPTED)


class SyncStatusView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request: Request, task_id: str) -> Response:
        # Celery не различает "неизвестный task_id" и "ещё не начатую задачу" —
        # оба случая отдают PENDING (ограничение AsyncResult API). На практике не
        # проблема: task_id всегда приходит из предшествующего POST .../run.
        result = AsyncResult(task_id)
        payload = {'state': result.state, 'result': None, 'error': None}
        if result.state == 'SUCCESS':
            payload['result'] = result.result
        elif result.state == 'FAILURE':
            payload['error'] = str(result.result)
        return Response(payload)
