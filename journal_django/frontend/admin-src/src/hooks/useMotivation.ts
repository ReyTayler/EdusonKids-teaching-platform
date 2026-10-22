import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type {
  Paginated, PersonalRate, PersonalRatePayload, PersonalRateUpdatePayload, RateVersion, RateVersionPayload,
  RateVersionsResponse, RecalculatedInfo,
} from '../lib/types';

const VERSIONS_KEY = ['payroll', 'rate-versions'] as const;
const PERSONAL_KEY = ['payroll', 'personal-rates'] as const;

export function useRateVersions() {
  return useQuery({
    queryKey: VERSIONS_KEY,
    queryFn: () => api<RateVersionsResponse>('GET', '/api/admin/payroll/rate-versions'),
  });
}

export interface PersonalRatesParams {
  teacher: string;
  group: string;
  page: number;
  page_size: number;
  sort_by: 'teacher' | 'group' | 'lesson_from';
  sort_dir: 'asc' | 'desc';
}

export function usePersonalRates(p: PersonalRatesParams) {
  const qs = new URLSearchParams({
    page: String(p.page), page_size: String(p.page_size),
    sort_by: p.sort_by, sort_dir: p.sort_dir,
  });
  if (p.teacher) qs.set('teacher', p.teacher);
  if (p.group) qs.set('group', p.group);
  return useQuery({
    queryKey: [...PERSONAL_KEY, p],
    queryFn: () => api<Paginated<PersonalRate>>('GET', `/api/admin/payroll/personal-rates?${qs}`),
    placeholderData: keepPreviousData,
  });
}

/**
 * Любая правка ставок пересчитывает начисления на сервере, поэтому после
 * мутации сбрасывается всё под ['payroll'] (версии, персональные ставки, списки
 * и сводки зарплаты) и кабинет-ключи, где показаны суммы.
 */
function useInvalidatePayroll() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ['payroll'] });
    qc.invalidateQueries({ queryKey: ['teacher-stats'] });
  };
}

export function useRateVersionMutations() {
  const invalidate = useInvalidatePayroll();
  const opts = { onSuccess: invalidate };
  return {
    create: useMutation({
      mutationFn: (body: RateVersionPayload) =>
        api<RateVersion & RecalculatedInfo>('POST', '/api/admin/payroll/rate-versions', body),
      ...opts,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Partial<RateVersionPayload> }) =>
        api<RateVersion & RecalculatedInfo>('PATCH', `/api/admin/payroll/rate-versions/${id}`, body),
      ...opts,
    }),
    remove: useMutation({
      mutationFn: (id: number) =>
        api<RecalculatedInfo>('DELETE', `/api/admin/payroll/rate-versions/${id}`),
      ...opts,
    }),
  };
}

export function usePersonalRateMutations() {
  const invalidate = useInvalidatePayroll();
  const opts = { onSuccess: invalidate };
  return {
    create: useMutation({
      mutationFn: (body: PersonalRatePayload) =>
        api<PersonalRate & RecalculatedInfo>('POST', '/api/admin/payroll/personal-rates', body),
      ...opts,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: PersonalRateUpdatePayload }) =>
        api<PersonalRate & RecalculatedInfo>('PATCH', `/api/admin/payroll/personal-rates/${id}`, body),
      ...opts,
    }),
    remove: useMutation({
      mutationFn: (id: number) =>
        api<RecalculatedInfo>('DELETE', `/api/admin/payroll/personal-rates/${id}`),
      ...opts,
    }),
    // Идемпотентны на сервере; отдают строку и число пересчитанных начислений.
    deactivate: useMutation({
      mutationFn: (id: number) =>
        api<PersonalRate & RecalculatedInfo>('POST', `/api/admin/payroll/personal-rates/${id}/deactivate`),
      ...opts,
    }),
    activate: useMutation({
      mutationFn: (id: number) =>
        api<PersonalRate & RecalculatedInfo>('POST', `/api/admin/payroll/personal-rates/${id}/activate`),
      ...opts,
    }),
  };
}
