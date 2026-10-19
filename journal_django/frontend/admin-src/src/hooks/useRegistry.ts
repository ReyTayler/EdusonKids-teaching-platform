import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Paginated, RegistrySegment, RegistryStudent, RegistrySummary } from '../lib/types';

// Сводка: KPI + «Поток дня» + счётчики сигналов. Кэшируется на бэке (снимок).
export function useRegistrySummary() {
  return useQuery({
    queryKey: ['registry', 'summary'],
    queryFn: () => api<RegistrySummary>('GET', '/api/admin/registry/summary'),
    staleTime: 30_000,
  });
}

export interface RegistryStudentsParams {
  page: number;
  page_size: number;
  sort_by: string;
  sort_dir: 'asc' | 'desc';
  segment: RegistrySegment;
  search: string;
  /** Режим «Без группы»: ученики без активного членства (те же колонки). */
  no_group: boolean;
}

function buildQuery(p: RegistryStudentsParams): string {
  const qs = new URLSearchParams();
  qs.set('page', String(p.page));
  qs.set('page_size', String(p.page_size));
  qs.set('sort_by', p.sort_by);
  qs.set('sort_dir', p.sort_dir);
  if (p.segment && p.segment !== 'all') qs.set('segment', p.segment);
  if (p.search) qs.set('search', p.search);
  if (p.no_group) qs.set('no_group', '1');
  return qs.toString();
}

// Серверно-пагинированный список учеников (подход B): по умолчанию — учащиеся,
// с no_group — те, у кого активной группы нет.
export function useRegistryStudents(params: RegistryStudentsParams) {
  return useQuery({
    queryKey: ['registry', 'students', params],
    queryFn: () =>
      api<Paginated<RegistryStudent>>('GET', `/api/admin/registry/students?${buildQuery(params)}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}
