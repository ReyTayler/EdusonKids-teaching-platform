import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

export interface RenewalFunnelStage {
  key: string;
  label: string;
  kind: string;
  cnt: number;
}

export interface RenewalFunnel {
  stages: RenewalFunnelStage[];
  renewal_rate_30d: number | null;
  won_30d: number;
  lost_30d: number;
}

/** Когорта месяца: сделки, чей цикл отработан в этом месяце (due_at/outcome_at). */
export interface RenewalMonthRow {
  month: string;        // 'YYYY-MM'
  matured: number;      // циклов созрело в месяце
  won: number;
  lost: number;
  in_progress: number;
  conversion: number | null;  // % won/(won+lost)
}

export function useRenewalAnalytics() {
  return useQuery({
    queryKey: ['renewals', 'analytics'],
    queryFn: () => api<RenewalFunnel>('GET', '/api/admin/renewals/analytics'),
    staleTime: 60_000,
  });
}

/** Когортная аналитика «Продления по месяцам» (group_by=month). */
export function useRenewalMonths() {
  return useQuery({
    queryKey: ['renewals', 'analytics', 'months'],
    queryFn: () => api<RenewalFunnel & { months: RenewalMonthRow[] }>(
      'GET', '/api/admin/renewals/analytics?group_by=month'),
    staleTime: 60_000,
  });
}
