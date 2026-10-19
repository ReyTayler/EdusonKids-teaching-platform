import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Paginated } from '../lib/types';
import type { TaskActivityItem, TaskFilters, TaskRow } from '../lib/tasks';
import { taskFilterQS } from '../lib/tasks';

// Ключи разведены на «справочники» (useTaskStructure.ts: воронки, стадии,
// исполнители — меняются раз в месяц) и «карточки» (этот файл —
// перетаскивание/создание/комментарии происходят постоянно). Мутация карточки
// не должна перезапрашивать справочники, иначе одно перетаскивание бьёт по
// сети столько же, сколько смена структуры доски.
//
// useTaskColumns (счётчики карточек по стадиям) живёт в useTaskStructure.ts,
// но ключ у него — под CARDS_KEY: счётчик меняется с каждым переносом карточки,
// то есть это данные о карточках, а не о структуре. Литерал ['tasks','cards']
// продублирован в обоих файлах намеренно — TanStack Query сравнивает ключи по
// значению, отдельная переменная ради этого не нужна.
const CARDS_KEY = ['tasks', 'cards'] as const;

/** Карточки одной колонки доски. page — 1-based, как в StandardPagination. */
export function useTaskColumnCards(stageId: number, filters: TaskFilters, page = 1) {
  return useQuery({
    queryKey: [...CARDS_KEY, 'column-cards', stageId, filters, page],
    queryFn: () => api<Paginated<TaskRow>>(
      'GET', `/api/admin/tasks/columns/${stageId}?page=${page}&${taskFilterQS(filters)}`),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });
}

export function useTaskList(filters: TaskFilters, page = 1, enabled = true) {
  return useQuery({
    queryKey: [...CARDS_KEY, 'list', filters, page],
    queryFn: () => api<Paginated<TaskRow>>(
      'GET', `/api/admin/tasks?page=${page}&${taskFilterQS(filters)}`),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });
}

export function useTaskWeek(dateFrom: string, dateTo: string, filters: TaskFilters, enabled = true) {
  return useQuery({
    queryKey: [...CARDS_KEY, 'week', dateFrom, dateTo, filters],
    queryFn: () => api<TaskRow[]>(
      'GET',
      `/api/admin/tasks/week?date_from=${dateFrom}&date_to=${dateTo}&${taskFilterQS(filters)}`),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });
}

export function useTask(id: number | undefined) {
  return useQuery({
    queryKey: [...CARDS_KEY, 'detail', id],
    queryFn: () => api<TaskRow>('GET', `/api/admin/tasks/${id}`),
    enabled: id !== undefined,
  });
}

export function useTaskActivity(id: number | undefined) {
  return useQuery({
    queryKey: [...CARDS_KEY, 'activity', id],
    queryFn: () => api<TaskActivityItem[]>('GET', `/api/admin/tasks/${id}/activity`),
    enabled: id !== undefined,
  });
}

/**
 * Мутации карточки. Сбрасываем только CARDS_KEY (колонки, список, неделя,
 * карточка, активность) — справочники (воронки/стадии/исполнители)
 * от переноса или создания задачи не меняются и трогать их незачем.
 */
export function useTaskMutations() {
  const qc = useQueryClient();
  const invalidate = () => { qc.invalidateQueries({ queryKey: CARDS_KEY }); };
  return {
    create: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<TaskRow>('POST', '/api/admin/tasks', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: number } & Record<string, unknown>) =>
        api<TaskRow>('PATCH', `/api/admin/tasks/${id}`, body),
      onSuccess: invalidate,
    }),
    move: useMutation({
      mutationFn: (v: { id: number; to_stage_id: number; resolution?: string }) =>
        api<TaskRow>('POST', `/api/admin/tasks/${v.id}/move`,
          { to_stage_id: v.to_stage_id, resolution: v.resolution }),
      onSuccess: invalidate,
    }),
    complete: useMutation({
      mutationFn: (v: { id: number; resolution: string }) =>
        api<TaskRow>('POST', `/api/admin/tasks/${v.id}/complete`, { resolution: v.resolution }),
      onSuccess: invalidate,
    }),
    comment: useMutation({
      mutationFn: (v: { id: number; body: string }) =>
        api<TaskActivityItem>('POST', `/api/admin/tasks/${v.id}/comment`, { body: v.body }),
      onSuccess: (_d, v) => {
        qc.invalidateQueries({ queryKey: [...CARDS_KEY, 'activity', v.id] });
      },
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/tasks/${id}`),
      onSuccess: invalidate,
    }),
  };
}
