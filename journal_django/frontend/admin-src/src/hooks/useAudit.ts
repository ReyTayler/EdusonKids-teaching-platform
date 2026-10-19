import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { AuditEntry, Paginated } from '../lib/types';

export function useAudit(query: string) {
  return useQuery({
    queryKey: ['audit', query],
    queryFn: () => api<Paginated<AuditEntry>>('GET', `/api/admin/audit-log${query}`),
    placeholderData: keepPreviousData,
  });
}
