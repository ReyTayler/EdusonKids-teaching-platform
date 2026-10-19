import { useQuery } from '@tanstack/react-query';
import { api } from '@shared/lib/api';
import type { GroupProgress } from '@shared/shared/progress/types';

/**
 * GET /api/group-progress?group=<name> — матрица посещаемости группы для
 * страницы группы. Контракт ответа = admin /api/admin/groups/:id/progress
 * (типы общие, shared/progress/types.ts); доступ гейтит сервер (владелец
 * группы или назначенный заменщик).
 */
export function useGroupProgress(group: string) {
  return useQuery({
    queryKey: ['groupProgress', group],
    queryFn: () => api<GroupProgress>('GET', `/api/group-progress?group=${encodeURIComponent(group)}`),
    enabled: !!group,
    staleTime: 30_000,
  });
}
