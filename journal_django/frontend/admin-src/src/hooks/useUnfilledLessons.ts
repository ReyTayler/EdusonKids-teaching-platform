import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Paginated, UnfilledLesson } from '../lib/types';

export interface UnfilledLessonsParams {
  page: number;
  page_size: number;
  teacher_id: number | null;
  sort_dir: 'asc' | 'desc';
}

function buildQuery(p: UnfilledLessonsParams): string {
  const qs = new URLSearchParams();
  qs.set('page', String(p.page));
  qs.set('page_size', String(p.page_size));
  qs.set('sort_dir', p.sort_dir);
  if (p.teacher_id != null) qs.set('teacher_id', String(p.teacher_id));
  return qs.toString();
}

/**
 * GET /api/admin/dashboard/unfilled-lessons — серверно-пагинированный список
 * просроченных незаполненных уроков (план + доп.уроки) по школе, опц. фильтр
 * по преподавателю, сортировка по дате (asc/desc, по умолчанию desc — новые сверху).
 */
export function useUnfilledLessons(params: UnfilledLessonsParams) {
  return useQuery({
    queryKey: ['unfilled-lessons', params],
    queryFn: () =>
      api<Paginated<UnfilledLesson>>('GET', `/api/admin/dashboard/unfilled-lessons?${buildQuery(params)}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}
