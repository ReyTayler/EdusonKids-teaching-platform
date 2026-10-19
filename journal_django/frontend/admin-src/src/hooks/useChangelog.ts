import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { ChangelogDetail, ChangelogOperation, Paginated, RevertResult } from '../lib/types';

export function useChangelogList(query: string) {
  return useQuery({
    queryKey: ['changelog', query],
    queryFn: () => api<Paginated<ChangelogOperation>>('GET', `/api/admin/changelog${query}`),
    placeholderData: keepPreviousData,
  });
}

export function useChangelogDetail(contextId: string | undefined) {
  return useQuery({
    queryKey: ['changelog', 'detail', contextId],
    queryFn: () => api<ChangelogDetail>('GET', `/api/admin/changelog/${contextId}`),
    enabled: !!contextId,
    retry: (failureCount, err) =>
      // 404 (несуществующий uuid) не ретраим — сразу показываем EmptyState.
      failureCount < 2 && !(err instanceof Error && 'status' in err && (err as { status: number }).status === 404),
  });
}

export function useChangelogMutations() {
  const qc = useQueryClient();
  return {
    revert: useMutation({
      mutationFn: (contextId: string) =>
        api<RevertResult>('POST', `/api/admin/changelog/${contextId}/revert`),
      onSuccess: () => {
        // Откат может задеть любую трекаемую модель (группы, уроки, оплаты...)
        // — инвалидируем весь кэш. Действие admin-only и редкое, лишние
        // refetch дешевле, чем показать устаревшие данные после отката.
        void qc.invalidateQueries();
      },
    }),
  };
}
