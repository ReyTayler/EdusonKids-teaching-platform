import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { ArchiveFilter, ArchivePayload } from '../lib/types';
interface Params { q: string; entity_type: ArchiveFilter; page: number; page_size: number }

export function useArchive(params: Params) {
  return useQuery({
    queryKey: ['archive', params],
    queryFn: () => {
      const qs = new URLSearchParams({ q: params.q, entity_type: params.entity_type,
        page: String(params.page), page_size: String(params.page_size) });
      return api<ArchivePayload>('GET', `/api/admin/archive?${qs}`);
    },
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}
