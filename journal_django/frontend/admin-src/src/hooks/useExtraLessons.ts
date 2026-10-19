import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { AbsenceResolution, ExtraLessonCreateResult, Paginated } from '../lib/types';

export interface ExtraLessonsListParams {
  page: number;
  page_size: number;
  sort_by: string;
  sort_dir: 'asc' | 'desc';
  filters: Record<string, string>;
}

function buildQuery(p: ExtraLessonsListParams): string {
  const qs = new URLSearchParams();
  qs.set('page', String(p.page));
  qs.set('page_size', String(p.page_size));
  qs.set('sort_by', p.sort_by);
  qs.set('sort_dir', p.sort_dir);
  for (const [k, v] of Object.entries(p.filters)) {
    if (v) qs.set(k, v);
  }
  return qs.toString();
}

export function useExtraLessons(params: ExtraLessonsListParams) {
  return useQuery({
    queryKey: ['extra-lessons', params],
    queryFn: () => api<Paginated<AbsenceResolution>>(
      'GET', `/api/admin/extra-lessons?${buildQuery(params)}`,
    ),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

/** Число необработанных пропусков (pending) — бейдж в сайдбаре. Разделяет
 *  префикс ['extra-lessons'] с мутациями раздела, поэтому create/burn/cancel/
 *  record/remove его инвалидируют автоматически; плюс лёгкий фон-рефетч. */
export function usePendingExtraLessonsCount() {
  return useQuery({
    queryKey: ['extra-lessons', 'pending-count'],
    queryFn: () => api<{ count: number }>('GET', '/api/admin/extra-lessons/pending-count'),
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useExtraLessonMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['extra-lessons'] });
    qc.invalidateQueries({ queryKey: ['lessons'] });
    qc.invalidateQueries({ queryKey: ['memberships'] });
    qc.invalidateQueries({ queryKey: ['calendar'] });
    // Матрица прогресса группы (['groups', id, 'progress']) строится из фактов —
    // доп.урок и сгорание её меняют. План НЕ трогаем: эти уроки не занимают
    // позицию курса (seq = NULL), плановых строк за ними нет.
    qc.invalidateQueries({ queryKey: ['groups'] });
  };
  return {
    create: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<ExtraLessonCreateResult>('POST', '/api/admin/extra-lessons', body),
      onSuccess: invalidate,
    }),
    // Ручной доп.урок СВЕРХ курса (kind='extra') — без пропуска, с явной группой
    // и опц. номером урока. Кейс: переведённому догнать прогресс группы.
    createManual: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<ExtraLessonCreateResult>('POST', '/api/admin/extra-lessons/manual', body),
      onSuccess: invalidate,
    }),
    cancel: useMutation({
      mutationFn: (id: number) =>
        api<AbsenceResolution>('POST', `/api/admin/extra-lessons/${id}/cancel`),
      onSuccess: invalidate,
    }),
    burn: useMutation({
      mutationFn: (id: number) =>
        api<{ lesson_id: number; payment: number }>('POST', `/api/admin/extra-lessons/${id}/burn`),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/extra-lessons/${id}`),
      onSuccess: invalidate,
    }),
  };
}
