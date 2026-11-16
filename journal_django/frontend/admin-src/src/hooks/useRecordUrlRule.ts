import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { RecordUrlRule, RecordUrlRuleInput } from '../lib/types';

const KEY = ['settings', 'record-url-rule'] as const;
const PATH = '/api/admin/lessons/record-url-rule';

export function useRecordUrlRule() {
  return useQuery({ queryKey: KEY, queryFn: () => api<RecordUrlRule>('GET', PATH) });
}

/** Правило сохраняется целиком одним PUT; ответ сервера кладём в кэш как есть. */
export function useSaveRecordUrlRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RecordUrlRuleInput) => api<RecordUrlRule>('PUT', PATH, body),
    onSuccess: (data) => { qc.setQueryData(KEY, data); },
  });
}
