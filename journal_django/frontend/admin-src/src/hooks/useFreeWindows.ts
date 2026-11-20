import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { FreeWindowsResponse } from '../lib/shared-types';

/**
 * Свободные окна всех преподавателей (вкладка «Сводка» календаря). Не пагинируется:
 * список — активные преподаватели школы, десятки строк. Меняется только при правке графика
 * или постоянного расписания, поэтому минута свежести — с запасом.
 */
export function useFreeWindows() {
  return useQuery<FreeWindowsResponse>({
    queryKey: ['admin-free-windows'],
    queryFn: () => api<FreeWindowsResponse>('GET', '/api/admin/work-schedule/free-windows'),
    staleTime: 60_000,
  });
}
