// journal_django/frontend/admin-src/src/hooks/useProdRefresh.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { ProdRefreshStatus } from '../lib/sync';

const KEY = ['sync', 'prod-refresh'];
const URL = '/api/admin/sync/prod-refresh';

/**
 * Статус и запуск мягкого обновления с прода. Пока обновление идёт — опрос раз в 3 с
 * (скрипт живёт минуты: снимок по SSH, таблицы, синк); иначе статус не опрашивается.
 */
export function useProdRefresh() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: KEY,
    queryFn: () => api<ProdRefreshStatus>('GET', URL),
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });
  const start = useMutation({
    mutationFn: () => api<ProdRefreshStatus>('POST', URL),
    onSuccess: (data) => qc.setQueryData(KEY, data),
    onSettled: () => qc.invalidateQueries({ queryKey: KEY }),
  });
  return { status, start };
}
