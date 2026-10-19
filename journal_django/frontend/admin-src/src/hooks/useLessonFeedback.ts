import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { LessonFeedbackRow, Paginated } from '../lib/types';

export interface LessonFeedbackParams {
  page: number;
  page_size: number;
  /** Ключи: group_id, teacher_id, date_from, date_to (YYYY-MM-DD). Пустые не шлём. */
  filters: Record<string, string>;
}

const ALLOWED = ['group_id', 'teacher_id', 'date_from', 'date_to'];

function buildQuery(p: LessonFeedbackParams): string {
  const qs = new URLSearchParams();
  qs.set('page', String(p.page));
  qs.set('page_size', String(p.page_size));
  // Белый список: в URL могут оказаться посторонние f.* — на бэк они не нужны.
  for (const k of ALLOWED) {
    const v = p.filters[k];
    if (v) qs.set(k, v);
  }
  return qs.toString();
}

export function useLessonFeedback(params: LessonFeedbackParams) {
  return useQuery({
    queryKey: ['lesson-feedback', params],
    queryFn: () => api<Paginated<LessonFeedbackRow>>('GET', `/api/admin/lesson-feedback?${buildQuery(params)}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}
