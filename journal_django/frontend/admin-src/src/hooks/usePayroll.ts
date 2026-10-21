import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { PayrollEntry, PayrollKind, PayrollSummaryRow, Paginated } from '../lib/types';

interface Filter { teacher_id?: number; date_from?: string; date_to?: string; }

function qs(f: Filter): string {
  const p: string[] = [];
  if (f.teacher_id) p.push(`teacher_id=${f.teacher_id}`);
  if (f.date_from) p.push(`date_from=${encodeURIComponent(f.date_from)}`);
  if (f.date_to) p.push(`date_to=${encodeURIComponent(f.date_to)}`);
  return p.length ? '?' + p.join('&') : '';
}

const KEY = ['payroll'] as const;

// ── Server-side pagination (PayrollPage list-mode) ──

export interface PayrollListParams {
  page: number;
  page_size: number;
  sort_by: string;
  sort_dir: 'asc' | 'desc';
  filters: Record<string, string>;
}

function buildPayrollQuery(p: PayrollListParams): string {
  const params = new URLSearchParams();
  params.set('page', String(p.page));
  params.set('page_size', String(p.page_size));
  params.set('sort_by', p.sort_by);
  params.set('sort_dir', p.sort_dir);
  for (const [k, v] of Object.entries(p.filters)) {
    if (v) params.set(`filter[${k}]`, v);
  }
  return params.toString();
}

export function usePayroll(params: PayrollListParams) {
  return useQuery({
    queryKey: [...KEY, 'list-paged', params],
    queryFn: () => api<Paginated<PayrollEntry>>('GET', `/api/admin/payroll?${buildPayrollQuery(params)}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function usePayrollSummary(filter: Filter = {}) {
  return useQuery({
    queryKey: [...KEY, 'summary', filter],
    queryFn: () => api<PayrollSummaryRow[]>('GET', '/api/admin/payroll/summary' + qs(filter)),
  });
}

/** Правка/сброс суммы. Урок и отмена — РАЗНЫЕ эндпоинты: слать правку отмены на
 *  /payroll/<id> нельзя, id в этих таблицах пересекаются и изменился бы чужой урок. */
export interface PayrollTarget { kind: PayrollKind; id: number; }

function payrollUrl(t: PayrollTarget): string {
  return t.kind === 'cancellation'
    ? `/api/admin/payroll/cancellations/${t.id}`
    : `/api/admin/payroll/${t.id}`;
}

export function usePayrollMutations() {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['payroll'] });
    qc.invalidateQueries({ queryKey: ['lessons'] });
    // Статистика преподавателя содержит заработок — после правки устаревает.
    qc.invalidateQueries({ queryKey: ['teacher-stats'] });
  };
  return {
    update: useMutation({
      mutationFn: ({ target, body }: { target: PayrollTarget; body: { payment?: number | string; penalty?: number | string } }) =>
        api<unknown>('PATCH', payrollUrl(target), body),
      onSuccess: refresh,
    }),
    reset: useMutation({
      mutationFn: (target: PayrollTarget) => api<unknown>('POST', `${payrollUrl(target)}/reset`),
      onSuccess: refresh,
    }),
  };
}
