import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { LessonCancellationRow, Paginated } from '../lib/types';

export interface GroupCancellationsParams {
  page: number;
  page_size: number;
}

/** Ключ — общий префикс для инвалидации из useCancelLesson. */
export const groupCancellationsKey = (groupId: number) => ['group-cancellations', groupId] as const;

/** Отмены уроков группы (новые сверху; порядок фиксирован на сервере). */
export function useGroupCancellations(groupId: number, params: GroupCancellationsParams) {
  return useQuery({
    queryKey: [...groupCancellationsKey(groupId), params],
    queryFn: () => api<Paginated<LessonCancellationRow>>(
      'GET',
      `/api/admin/groups/${groupId}/cancellations?page=${params.page}&page_size=${params.page_size}`,
    ),
    placeholderData: keepPreviousData,
    enabled: Number.isFinite(groupId) && groupId > 0,
    staleTime: 30_000,
  });
}
