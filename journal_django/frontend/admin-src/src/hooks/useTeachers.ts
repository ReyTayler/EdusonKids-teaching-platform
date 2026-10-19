import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Teacher } from '../lib/types';

const KEY = ['teachers'] as const;

export function useTeachers(includeInactive = false) {
  return useQuery({
    queryKey: [...KEY, { includeInactive }],
    queryFn: () => api<Teacher[]>('GET', `/api/admin/teachers${includeInactive ? '?include_inactive=1' : ''}`),
  });
}

export function useTeacher(id: number) {
  return useQuery({
    queryKey: [...KEY, id],
    queryFn: () => api<Teacher>('GET', `/api/admin/teachers/${id}`),
    enabled: Number.isFinite(id) && id > 0,
  });
}

export function useTeacherMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['teachers'] });
    qc.invalidateQueries({ queryKey: ['archive'] });
  };
  return {
    create: useMutation({
      mutationFn: (body: Partial<Teacher>) => api<Teacher>('POST', '/api/admin/teachers', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Partial<Teacher> }) =>
        api<Teacher>('PATCH', `/api/admin/teachers/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/teachers/${id}`),
      onSuccess: invalidate,
    }),
  };
}
