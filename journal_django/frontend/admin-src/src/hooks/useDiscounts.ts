import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Discount } from '../lib/types';

const KEY = ['discounts'] as const;

export function useDiscounts(includeInactive = false) {
  return useQuery({
    queryKey: [...KEY, { includeInactive }],
    queryFn: () => api<Discount[]>('GET', `/api/admin/discounts${includeInactive ? '?include_inactive=1' : ''}`),
    staleTime: 30_000,
  });
}

export function useDiscount(id: number) {
  return useQuery({
    queryKey: [...KEY, id],
    queryFn: () => api<Discount>('GET', `/api/admin/discounts/${id}`),
    enabled: Number.isFinite(id) && id > 0,
  });
}

export function useDiscountMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['discounts'] });
  return {
    create: useMutation({
      mutationFn: (body: { name: string; amount: number }) =>
        api<Discount>('POST', '/api/admin/discounts', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Partial<Discount> }) =>
        api<Discount>('PATCH', `/api/admin/discounts/${id}`, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/discounts/${id}`),
      onSuccess: invalidate,
    }),
  };
}
