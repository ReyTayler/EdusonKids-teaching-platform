import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { GroupProgress } from '../shared/progress/types';

/** Типы контракта прогресса переехали в shared/progress/types.ts (общие с teacher SPA). */
export type { GroupProgress, ProgressSlot, ProgressStudent } from '../shared/progress/types';

/** Обзорная матрица посещаемости группы (вкладка «Прогресс»).
 *  Грузится лениво — панель таба монтируется только при активации. */
export function useGroupProgress(groupId: number) {
  return useQuery({
    queryKey: ['groups', groupId, 'progress'],
    queryFn: () => api<GroupProgress>('GET', `/api/admin/groups/${groupId}/progress`),
    enabled: Number.isFinite(groupId) && groupId > 0,
    staleTime: 30_000,
  });
}
