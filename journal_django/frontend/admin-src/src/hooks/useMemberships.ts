import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { GroupMembership } from '../lib/types';

interface Filter { group_id?: number; student_id?: number; include_inactive?: boolean; }

function qs(f: Filter): string {
  const p: string[] = [];
  if (f.group_id) p.push(`group_id=${f.group_id}`);
  if (f.student_id) p.push(`student_id=${f.student_id}`);
  if (f.include_inactive) p.push('include_inactive=1');
  return p.length ? '?' + p.join('&') : '';
}

const KEY = ['memberships'] as const;

export function useMemberships(filter: Filter) {
  return useQuery({
    queryKey: [...KEY, filter],
    queryFn: () => api<GroupMembership[]>('GET', '/api/admin/memberships' + qs(filter)),
    enabled: !!(filter.group_id || filter.student_id),
  });
}

export function useMembershipMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['memberships'] });
    qc.invalidateQueries({ queryKey: ['students'] });
    qc.invalidateQueries({ queryKey: ['groups'] });
  };
  return {
    create: useMutation({
      mutationFn: (body: { student_id: number; group_id: number }) =>
        api<GroupMembership>('POST', '/api/admin/memberships', body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) =>
        api<void>('DELETE', `/api/admin/memberships/${id}`),
      onSuccess: invalidate,
    }),
  };
}
