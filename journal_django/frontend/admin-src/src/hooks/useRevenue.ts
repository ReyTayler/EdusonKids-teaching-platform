import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { RevenueData } from '../lib/types';
import { buildQuery, type DashboardParams } from './useDashboard';

/** Revenue / Orders за период + разбивка по дням и месяцам (дашборд «Финансы»). */
export function useRevenue(params: DashboardParams = {}) {
  return useQuery({
    queryKey: ['dashboard-revenue', params.from || '', params.to || ''],
    queryFn: () => api<RevenueData>('GET', `/api/admin/dashboard/revenue${buildQuery(params)}`),
    staleTime: 30_000,
    // Смена периода не сбрасывает график в скелетон — старые данные видны до ответа.
    placeholderData: keepPreviousData,
  });
}
