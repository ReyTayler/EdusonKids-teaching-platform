import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api, fetchAllPages } from '../lib/api';
import type { Group, GroupScheduleSlot, Paginated } from '../lib/types';

export interface GroupPayload {
  name: string;
  direction_id: number;
  teacher_id: number;
  is_individual: boolean;
  lesson_duration_minutes: 45 | 60 | 90;
  lessons_per_week: number;
  lessons_total?: number | null;
  group_start_date?: string | null;
  vk_chat?: string | null;
  slots: Pick<GroupScheduleSlot, 'day_of_week' | 'start_time'>[];
  active?: boolean;
}

const KEY = ['groups'] as const;

// ──────────────────────────────────────────────────────
// Для форм и деталей, где нужен полный список групп (селекторы, detail-страницы).
// Бэк теперь всегда отдаёт Paginated — распаковываем .rows здесь, page_size 2000 хватит.
// ──────────────────────────────────────────────────────
export function useGroupsAll(includeInactive = false) {
  return useQuery({
    queryKey: [...KEY, 'all', { includeInactive }],
    queryFn: () => {
      const qs = new URLSearchParams({ sort_by: 'name', sort_dir: 'asc' });
      if (includeInactive) qs.set('include_inactive', '1');
      // Все страницы, а не первая: бэкенд зажимает page_size до 500 (см. fetchAllPages).
      return fetchAllPages<Group>('/api/admin/groups', qs);
    },
    staleTime: 60_000,
  });
}

// ──────────────────────────────────────────────────────
// Для GroupsListPage (server-side pagination).
// ──────────────────────────────────────────────────────
export interface GroupsListParams {
  page: number;
  page_size: number;
  sort_by: string;
  sort_dir: 'asc' | 'desc';
  filters: Record<string, string>;
}

function buildGroupsQuery(p: GroupsListParams): string {
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

export function useGroups(params: GroupsListParams) {
  return useQuery({
    queryKey: [...KEY, 'list', params],
    queryFn: () => api<Paginated<Group>>('GET', `/api/admin/groups?${buildGroupsQuery(params)}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useGroup(id: number) {
  return useQuery({
    queryKey: [...KEY, id],
    queryFn: () => api<Group>('GET', `/api/admin/groups/${id}`),
    enabled: Number.isFinite(id) && id > 0,
  });
}

export function useGroupMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['groups'] });
    qc.invalidateQueries({ queryKey: ['archive'] });
    // План живёт под отдельным ключом ['group-plan', id] — префикс ['groups'] его
    // НЕ накрывает. Правка группы может двигать план на сервере (смена длины курса
    // lessons_total дописывает или урезает хвост), поэтому инвалидируем явно,
    // иначе вкладка «Расписание» показывает прежние занятия до перезагрузки.
    qc.invalidateQueries({ queryKey: ['group-plan'] });
    // Карточка преподавателя держит свой срез групп под ключом ['teacher-groups']
    // со staleTime 60 c. Без явной инвалидации смена преподавателя у группы не
    // видна на его карточке целую минуту — группа остаётся в списке прежнего.
    qc.invalidateQueries({ queryKey: ['teacher-groups'] });
    qc.invalidateQueries({ queryKey: ['teacher-stats'] });
  };
  return {
    create: useMutation({
      mutationFn: (body: GroupPayload) => api<Group>('POST', '/api/admin/groups', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Partial<GroupPayload> }) =>
        api<Group>('PATCH', `/api/admin/groups/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/groups/${id}`),
      onSuccess: invalidate,
    }),
  };
}
