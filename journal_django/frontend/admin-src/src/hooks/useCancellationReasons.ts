import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type {
  CancellationReason, CancellationReasonCreatePayload, CancellationReasonUpdatePayload,
  CancellationReasonsResponse, ID,
} from '../lib/types';

export const CANCELLATION_REASONS_KEY = ['cancellation-reasons'] as const;

/** Активные причины по сторонам. Поле compensation приходит только superadmin. */
export function useCancellationReasons() {
  return useQuery({
    queryKey: CANCELLATION_REASONS_KEY,
    queryFn: () => api<CancellationReasonsResponse>('GET', '/api/admin/cancellation-reasons'),
    staleTime: 60_000,
  });
}

/** Справочник правит только superadmin (экран «Настройки → Отмены уроков»). */
export function useCancellationReasonMutations() {
  const qc = useQueryClient();
  // onSettled: после ошибки (например, причину уже удалил другой админ) список тоже устарел.
  const opts = { onSettled: () => qc.invalidateQueries({ queryKey: CANCELLATION_REASONS_KEY }) };
  return {
    create: useMutation({
      mutationFn: (body: CancellationReasonCreatePayload) =>
        api<CancellationReason>('POST', '/api/admin/cancellation-reasons', body),
      ...opts,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: ID; body: CancellationReasonUpdatePayload }) =>
        api<CancellationReason>('PATCH', `/api/admin/cancellation-reasons/${id}`, body),
      ...opts,
    }),
    remove: useMutation({
      mutationFn: (id: ID) => api<void>('DELETE', `/api/admin/cancellation-reasons/${id}`),
      ...opts,
    }),
  };
}
