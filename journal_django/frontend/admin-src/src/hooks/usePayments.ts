import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Payment } from '../lib/types';

const KEY = ['payments'] as const;

export interface PaymentFilters {
  student_id?: number;
  direction_id?: number;
  from?: string;
  to?: string;
}

export interface PaymentCreateInput {
  student_id: number;
  direction_id: number;
  lessons_count: number;
  total_amount: number;
  paid_at: string;
  note?: string | null;
  // 'extra' — доплата за доп.урок сверх курса (мимо лимита). По умолчанию purchase.
  kind?: 'purchase' | 'extra';
}

export interface PaymentDeleteResult {
  deleted: true;
  new_balance: number;
  warning?: 'balance_negative';
}

function buildQuery(f: PaymentFilters | undefined): string {
  if (!f) return '';
  const params = new URLSearchParams();
  if (f.student_id)   params.set('student_id',   String(f.student_id));
  if (f.direction_id) params.set('direction_id', String(f.direction_id));
  if (f.from)         params.set('from', f.from);
  if (f.to)           params.set('to', f.to);
  const s = params.toString();
  return s ? `?${s}` : '';
}

export function usePayments(filters?: PaymentFilters) {
  return useQuery({
    queryKey: [...KEY, filters || {}],
    queryFn: () => api<Payment[]>('GET', `/api/admin/payments${buildQuery(filters)}`),
  });
}

export function usePaymentMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['payments'] });
    qc.invalidateQueries({ queryKey: ['students'] });
    qc.invalidateQueries({ queryKey: ['student-balance'] });
    qc.invalidateQueries({ queryKey: ['directions'] });
    // Оплата закрывает/переоткрывает сделку продления (сигналы на бэке) —
    // доска и карточки должны обновиться сразу, не дожидаясь staleTime.
    qc.invalidateQueries({ queryKey: ['renewals'] });
  };
  return {
    create: useMutation({
      mutationFn: (body: PaymentCreateInput) =>
        api<Payment>('POST', '/api/admin/payments', body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) =>
        api<PaymentDeleteResult>('DELETE', `/api/admin/payments/${id}`),
      onSuccess: invalidate,
    }),
    refund: useMutation({
      mutationFn: (studentId: number) =>
        // refunds — по строке на каждое направление, из партий которого возвращаются
        // деньги (иначе лимит курса не освобождается). refunded_amount — общая сумма.
        api<{ refunds: Payment[]; new_balance: number; refunded_amount: number }>(
          'POST', `/api/admin/students/${studentId}/refund`, {}),
      onSuccess: invalidate,
    }),
    // Доплата к абонементу (kind='surcharge'): деньги без уроков, поднимающие цену
    // конкретного блока родительской оплаты. См. docs/superpowers/specs/
    // 2026-07-28-course-surcharge-design.md.
    surcharge: useMutation({
      mutationFn: (body: {
        student_id: number;
        parent_payment_id: number;
        subscription_index: number;
        total_amount: string;
        paid_at: string;
        note?: string | null;
      }) => api<Payment>('POST', '/api/admin/payments', { ...body, kind: 'surcharge' }),
      onSuccess: invalidate,
    }),
  };
}
