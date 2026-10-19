import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { DashboardData } from '../lib/types';

export interface DashboardParams {
  from?: string;
  to?: string;
}

export function buildQuery(p: DashboardParams): string {
  const params = new URLSearchParams();
  if (p.from) params.set('from', p.from);
  if (p.to) params.set('to', p.to);
  const s = params.toString();
  return s ? `?${s}` : '';
}

export function useDashboard(params: DashboardParams = {}) {
  return useQuery({
    queryKey: ['dashboard', params.from || '', params.to || ''],
    queryFn: () => api<DashboardData>('GET', `/api/admin/dashboard${buildQuery(params)}`),
    staleTime: 30_000,
  });
}
