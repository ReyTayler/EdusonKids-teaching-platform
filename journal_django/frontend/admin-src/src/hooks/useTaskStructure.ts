import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type {
  StageCategory, TaskAssignee, TaskBoard, TaskColumnCount, TaskStage,
} from '../lib/tasks';

// «Справочники» — воронки/стадии/исполнители, меняются раз в месяц.
// Карточные мутации (useTasks.ts) их не трогают, поэтому у них отдельный
// корень ключа — иначе одно перетаскивание карточки перезапрашивало бы и это.
const REF_KEY = ['tasks', 'ref'] as const;

// Счётчики колонок меняются с каждым переносом/созданием карточки — это
// данные О КАРТОЧКАХ, а не справочник, поэтому ключ у них под CARDS_KEY, а не
// REF_KEY (см. комментарий в useTasks.ts — литерал продублирован намеренно,
// TanStack Query сравнивает ключи по значению).
const CARDS_KEY = ['tasks', 'cards'] as const;

export function useTaskBoards() {
  return useQuery({
    queryKey: [...REF_KEY, 'boards'],
    queryFn: () => api<TaskBoard[]>('GET', '/api/admin/tasks/boards'),
    staleTime: 60_000,
  });
}

export function useTaskStages(boardId: number | undefined) {
  return useQuery({
    queryKey: [...REF_KEY, 'stages', boardId],
    queryFn: () => api<TaskStage[]>('GET', `/api/admin/tasks/boards/${boardId}/stages`),
    enabled: boardId !== undefined,
    staleTime: 60_000,
  });
}

/** Колонки со счётчиками. Отдельно от карточек — доска не грузится одним запросом. */
export function useTaskColumns(boardId: number | undefined) {
  return useQuery({
    queryKey: [...CARDS_KEY, 'columns', boardId],
    queryFn: () => api<TaskColumnCount[]>('GET', `/api/admin/tasks/boards/${boardId}/columns`),
    enabled: boardId !== undefined,
    staleTime: 15_000,
  });
}

export function useTaskAssignees() {
  return useQuery({
    queryKey: [...REF_KEY, 'assignees'],
    queryFn: () => api<TaskAssignee[]>('GET', '/api/admin/tasks/assignees'),
    staleTime: 300_000,
  });
}

/** Мутации воронок. Доступны только суперадмину — маршрут закрыт RequireRole. */
export function useBoardMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: [...REF_KEY, 'boards'] });
  return {
    create: useMutation({
      mutationFn: (body: { name: string; description?: string | null; sort_order?: number }) =>
        api<TaskBoard>('POST', '/api/admin/tasks/boards', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: {
        id: number; name?: string; description?: string | null; sort_order?: number;
      }) => api<TaskBoard>('PATCH', `/api/admin/tasks/boards/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/tasks/boards/${id}`),
      onSuccess: invalidate,
    }),
  };
}

/**
 * Мутации структуры (стадии). Доступны только суперадмину — маршрут закрыт
 * RequireRole. Сбрасывают и справочник стадий, и счётчики колонок — новая/
 * удалённая/переставленная стадия меняет сам набор колонок на доске, это не
 * тот случай, который может подождать до следующей карточной мутации.
 */
export function useStageMutations(boardId: number | undefined) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: [...REF_KEY, 'stages', boardId] });
    qc.invalidateQueries({ queryKey: [...CARDS_KEY, 'columns', boardId] });
  };
  return {
    create: useMutation({
      mutationFn: (body: { label: string; category: StageCategory; color?: string | null }) =>
        api<TaskStage>('POST', `/api/admin/tasks/boards/${boardId}/stages`, body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: number; label?: string; category?: StageCategory; color?: string | null }) =>
        api<TaskStage>('PATCH', `/api/admin/tasks/stages/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/tasks/stages/${id}`),
      onSuccess: invalidate,
    }),
    reorder: useMutation({
      // Воронка идёт в адресе, а не выводится из набора стадий: раньше бэкенд
      // доставал её через boards.pop() и на пустом order падал в 500.
      // Набор при этом обязан быть ПОЛНЫМ — иначе 400 incomplete_stage_set.
      mutationFn: ({ boardId, order }: { boardId: number; order: number[] }) =>
        api<TaskStage[]>(
          'POST', `/api/admin/tasks/boards/${boardId}/stages/reorder`, { order }),
      onSuccess: invalidate,
    }),
  };
}
