import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Direction } from '../lib/types';

const KEY = ['directions'] as const;

export function useDirections(includeInactive = false) {
  return useQuery({
    queryKey: [...KEY, { includeInactive }],
    queryFn: () => api<Direction[]>('GET', `/api/admin/directions${includeInactive ? '?include_inactive=1' : ''}`),
  });
}

export function useDirection(id: number) {
  return useQuery({
    queryKey: [...KEY, id],
    queryFn: () => api<Direction>('GET', `/api/admin/directions/${id}`),
    enabled: Number.isFinite(id) && id > 0,
  });
}

export function useDirectionMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['directions'] });
    qc.invalidateQueries({ queryKey: ['archive'] });
  };
  return {
    create: useMutation({
      mutationFn: (body: Partial<Direction>) => api<Direction>('POST', '/api/admin/directions', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Partial<Direction> }) =>
        api<Direction>('PATCH', `/api/admin/directions/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/directions/${id}`),
      onSuccess: invalidate,
    }),
  };
}
