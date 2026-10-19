// journal_django/frontend/admin-src/src/hooks/useSyncAction.ts
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { SyncAction, SyncRunResponse, SyncStatus } from '../lib/sync';

const TERMINAL_STATES = new Set(['SUCCESS', 'FAILURE']);

/**
 * Инкапсулирует триггер (POST .../run) и поллинг статуса (GET .../status/<task_id>)
 * для одного действия раздела «Синхро». taskId живёт только в памяти компонента —
 * уход со страницы сбрасывает его (история запусков сознательно не персистится).
 */
export function useSyncAction(action: SyncAction) {
  const [taskId, setTaskId] = useState<string | null>(null);

  const trigger = useMutation({
    mutationFn: (dryRun: boolean) =>
      api<SyncRunResponse>('POST', `/api/admin/sync/${action}/run`, { dry_run: dryRun }),
    onSuccess: (data) => setTaskId(data.task_id),
  });

  const statusQuery = useQuery({
    queryKey: ['sync-status', taskId],
    queryFn: () => api<SyncStatus>('GET', `/api/admin/sync/status/${taskId}`),
    enabled: taskId != null,
    refetchInterval: (query) => {
      if (query.state.status === 'error') return false;
      const state = query.state.data?.state;
      return state && TERMINAL_STATES.has(state) ? false : 1500;
    },
  });

  const isPolling =
    taskId != null &&
    statusQuery.status !== 'error' &&
    !(statusQuery.data && TERMINAL_STATES.has(statusQuery.data.state));

  return {
    run: (dryRun: boolean) => trigger.mutate(dryRun),
    isTriggering: trigger.isPending,
    triggerError: trigger.error,
    status: statusQuery.data ?? null,
    statusError: statusQuery.error,
    isPolling,
  };
}
