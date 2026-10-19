import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { RenewalStage } from '../lib/renewals';

export function useRenewalStages() {
  return useQuery({
    queryKey: ['renewals', 'stages'],
    queryFn: () => api<RenewalStage[]>('GET', '/api/admin/renewals/stages'),
    staleTime: 60_000,
  });
}

export function useRenewalStageMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['renewals'] });
  return {
    create: useMutation({
      mutationFn: (body: Partial<RenewalStage>) =>
        api<RenewalStage>('POST', '/api/admin/renewals/stages', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Partial<RenewalStage> }) =>
        api<RenewalStage>('PATCH', `/api/admin/renewals/stages/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/renewals/stages/${id}`),
      onSuccess: invalidate,
    }),
    reorder: useMutation({
      mutationFn: (order: number[]) =>
        api<RenewalStage[]>('POST', '/api/admin/renewals/stages/reorder', { order }),
      onSuccess: invalidate,
    }),
  };
}
