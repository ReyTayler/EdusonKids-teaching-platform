import { useQuery } from '@tanstack/react-query';
import { api } from '@shared/lib/api';
import type { GroupDirectionsResponse } from '../lib/types';

/**
 * GET /api/group-directions — карта ВСЕХ активных групп (имя → направление+
 * цвет) для точного окрашивания уроков в календаре/отчёте (вместо эвристики
 * по имени группы). Справочник меняется редко — staleTime покрупнее, как у
 * useTeacherData.
 */
export function useGroupDirections() {
  return useQuery<GroupDirectionsResponse>({
    queryKey: ['groupDirections'],
    queryFn: () => api<GroupDirectionsResponse>('GET', '/api/group-directions'),
    staleTime: 5 * 60_000,
  });
}
