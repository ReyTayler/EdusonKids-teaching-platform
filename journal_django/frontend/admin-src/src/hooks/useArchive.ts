import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Teacher, Group, Direction, Paginated } from '../lib/types';

export interface ArchivePayload {
  teachers: Teacher[];
  groups: Group[];
  directions: Direction[];
}

export function useArchive() {
  return useQuery({
    queryKey: ['archive'],
    queryFn: async (): Promise<ArchivePayload> => {
      // teachers/directions отдают ПЛОСКИЙ массив и понимают include_inactive=1.
      // groups — ПАГИНИРОВАННЫЙ ({rows,total,...}) и include_inactive НЕ знает: у него
      // серверный фильтр filter[active]. Поэтому берём архивные напрямую сервером
      // (filter[active]=false) и распаковываем .rows. Раньше здесь groups
      // типизировались как Group[] и вызывался groups.filter(...) → TypeError на
      // объекте → весь Promise.all падал → архив пустой целиком.
      const [teachers, groupsPage, directions] = await Promise.all([
        api<Teacher[]>('GET', '/api/admin/teachers?include_inactive=1'),
        api<Paginated<Group>>('GET', '/api/admin/groups?filter[active]=false&page_size=500'),
        api<Direction[]>('GET', '/api/admin/directions?include_inactive=1'),
      ]);
      return {
        teachers:   teachers.filter((r) => r.active === false),
        groups:     groupsPage.rows,   // сервер уже вернул только active=false
        directions: directions.filter((r) => r.active === false),
      };
    },
  });
}
