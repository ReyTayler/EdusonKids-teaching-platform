// journal_django/frontend/admin-src/src/hooks/useReports.ts
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { ReportRunResponse, ReportTaskStatus } from '../lib/reports';

const TERMINAL = new Set(['SUCCESS', 'FAILURE']);

/**
 * Инкапсулирует генерацию одного отчёта: POST .../run → поллинг статуса →
 * скачивание сразу по готовности. Отчёт нигде не хранится: файл живёт только в
 * celery result backend и тянется по task_id. taskId держим в памяти компонента.
 */
export function useReportRun(reportType: string) {
  const [taskId, setTaskId] = useState<string | null>(null);

  const trigger = useMutation({
    mutationFn: (params: Record<string, unknown>) =>
      api<ReportRunResponse>('POST', `/api/admin/reports/${reportType}/run`, params),
    onSuccess: (data) => setTaskId(data.task_id),
  });

  const statusQuery = useQuery({
    queryKey: ['report-status', taskId],
    queryFn: () => api<ReportTaskStatus>('GET', `/api/admin/reports/status/${taskId}`),
    enabled: taskId != null,
    refetchInterval: (query) => {
      if (query.state.status === 'error') return false;
      const state = query.state.data?.state;
      return state && TERMINAL.has(state) ? false : 1200;
    },
  });

  const status = statusQuery.data ?? null;
  const isBusy =
    trigger.isPending ||
    (taskId != null && statusQuery.status !== 'error' && !(status && TERMINAL.has(status.state)));

  const triggerError = trigger.error instanceof Error ? trigger.error : null;

  return {
    run: (params: Record<string, unknown>) => trigger.mutate(params),
    taskId,
    status,
    isBusy,
    triggerError,
    statusError: statusQuery.error instanceof Error ? statusQuery.error : null,
  };
}

/**
 * Скачать готовый отчёт по task_id: GET (safe, CSRF не нужен) c credentials,
 * затем браузерная загрузка через object URL. api() тут не годится — он парсит JSON.
 */
export async function downloadReport(taskId: string, filename: string | null): Promise<void> {
  const res = await fetch(`/api/admin/reports/download/${taskId}`, { credentials: 'include' });
  if (!res.ok) throw new Error('Не удалось скачать отчёт');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename ?? `report_${taskId}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
