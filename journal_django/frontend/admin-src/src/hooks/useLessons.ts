import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Lesson, LessonFull, Paginated } from '../lib/types';

// ── Для LessonGrid (client-side, фильтр по group_id / teacher_id) ──

interface ListFilter {
  group_id?: number;
  teacher_id?: number;
  date_from?: string;
  date_to?: string;
}

function buildQs(f: ListFilter): string {
  const parts: string[] = [];
  if (f.group_id) parts.push(`group_id=${f.group_id}`);
  if (f.teacher_id) parts.push(`teacher_id=${f.teacher_id}`);
  if (f.date_from) parts.push(`date_from=${encodeURIComponent(f.date_from)}`);
  if (f.date_to) parts.push(`date_to=${encodeURIComponent(f.date_to)}`);
  return parts.length ? '?' + parts.join('&') : '';
}

/** Загружает все уроки группы (без UI-пагинации). Используется в LessonGrid.
 *  Бэк после P1 всегда отдаёт Paginated<Lesson> — распаковываем .rows здесь,
 *  чтобы потребитель видел Lesson[] как раньше. page_size=1000 — хватит для одной группы. */
export function useLessonsForGroup(filter: ListFilter = {}) {
  return useQuery({
    queryKey: ['lessons', 'group-filter', filter],
    queryFn: async () => {
      const res = await api<Paginated<Lesson>>(
        'GET',
        '/api/admin/lessons?page_size=1000' + buildQs(filter).replace(/^\?/, '&'),
      );
      return res.rows;
    },
  });
}

// ── Для LessonsListPage (server-side pagination) ──

export interface LessonsListParams {
  page: number;
  page_size: number;
  sort_by: string;
  sort_dir: 'asc' | 'desc';
  filters: Record<string, string>;
}

function buildLessonsQuery(p: LessonsListParams): string {
  const qs = new URLSearchParams();
  qs.set('page', String(p.page));
  qs.set('page_size', String(p.page_size));
  qs.set('sort_by', p.sort_by);
  qs.set('sort_dir', p.sort_dir);
  for (const [k, v] of Object.entries(p.filters)) {
    if (v) qs.set(`filter[${k}]`, v);
  }
  return qs.toString();
}

/** Загружает уроки с server-side пагинацией. Используется в LessonsListPage. */
export function useLessons(params: LessonsListParams) {
  return useQuery({
    queryKey: ['lessons', params],
    queryFn: () => api<Paginated<Lesson>>('GET', `/api/admin/lessons?${buildLessonsQuery(params)}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useLessonFull(id: number | null) {
  return useQuery({
    queryKey: ['lessons', id, 'full'],
    queryFn: () => api<LessonFull>('GET', `/api/admin/lessons/${id}`),
    enabled: Number.isFinite(id) && (id || 0) > 0,
  });
}

/** student_id, помеченных «неоплачиваемый пропуск» на слот (в т.ч. ещё не проведённый). */
export function useLessonSkips(groupId: number, lessonNumber: number, enabled = true) {
  return useQuery({
    queryKey: ['lesson-skips', groupId, lessonNumber],
    queryFn: () => api<{ student_ids: number[] }>(
      'GET', `/api/admin/groups/${groupId}/lesson-skips?lesson_number=${lessonNumber}`),
    enabled: enabled && groupId > 0,
  });
}

export function useLessonMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['lessons'] });
    qc.invalidateQueries({ queryKey: ['payroll'] });
    qc.invalidateQueries({ queryKey: ['memberships'] });
    qc.invalidateQueries({ queryKey: ['students'] });
    qc.invalidateQueries({ queryKey: ['lesson-skips'] });
    // Запись/правка урока двигает и план, и матрицу прогресса: link_facts
    // переводит плановое занятие в «проведено», прогресс пересчитывается из
    // фактов. План лежит под своим ключом ['group-plan', id] — префикс ['groups']
    // его не накрывает, поэтому инвалидируем оба; иначе «Расписание» и «Прогресс»
    // показывают дореактивное состояние до перезагрузки страницы.
    qc.invalidateQueries({ queryKey: ['groups'] });
    qc.invalidateQueries({ queryKey: ['group-plan'] });
  };
  return {
    create: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<LessonFull>('POST', '/api/admin/lessons', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
        api<Lesson>('PATCH', `/api/admin/lessons/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/lessons/${id}`),
      onSuccess: invalidate,
    }),
    // present + is_free: «бесплатно» = present:true, is_free:true. is_free
    // необязателен (по умолчанию сервер трактует как false). Позволяет менять
    // исход ячейки уже проведённого урока (в т.ч. проставить бесплатный постфактум).
    // allow_debt — «отметить, невзирая на долг»: только суперадмин, бэк проверяет
    // роль и отвечает 403 остальным. Занятие остаётся платным (спишется с
    // баланса) — это НЕ is_free.
    toggleAttendance: useMutation({
      mutationFn: ({ lessonId, studentId, present, is_free, allow_debt }:
        { lessonId: number; studentId: number; present: boolean; is_free?: boolean;
          allow_debt?: boolean }) =>
        api<{ ok: true }>('PATCH', `/api/admin/lessons/${lessonId}/attendance/${studentId}`,
          { present, is_free: !!is_free, allow_debt: !!allow_debt }),
      onSuccess: invalidate,
    }),
    // Исход «неоплачиваемый пропуск»: поставить/снять (в т.ч. на проведённом уроке).
    setUnpaidSkip: useMutation({
      mutationFn: ({ lessonId, studentId, value }:
        { lessonId: number; studentId: number; value: boolean }) =>
        api<{ ok: true }>('PATCH', `/api/admin/lessons/${lessonId}/unpaid-skip/${studentId}`, { value }),
      onSuccess: invalidate,
    }),
    // Пометка «неоплачиваемый пропуск» на СЛОТ группы — работает и на ещё не
    // проведённом уроке (без даты). Вариант A.
    setGroupLessonSkip: useMutation({
      mutationFn: ({ groupId, studentId, lessonNumber, value }:
        { groupId: number; studentId: number; lessonNumber: number; value: boolean }) =>
        api<{ ok: true }>('PATCH', `/api/admin/groups/${groupId}/lesson-skips`,
          { student_id: studentId, lesson_number: lessonNumber, value }),
      onSuccess: invalidate,
    }),
  };
}
