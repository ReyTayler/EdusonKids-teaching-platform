import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Account, Paginated, Role } from '../lib/types';

const KEY = ['accounts'] as const;

export function useAccounts(query: string) {
  return useQuery({
    queryKey: [...KEY, query],
    queryFn: () => api<Paginated<Account>>('GET', `/api/admin/accounts${query}`),
    placeholderData: keepPreviousData,
  });
}

/** Ответ выписки invite (create / reset-password / regenerate). */
export interface InviteResult {
  invite_url: string;
  expires_at: string;
}

export interface CreatedAccount extends InviteResult {
  id: number;
  email: string;
  role: Role;
  teacher_id: number | null;
  full_name?: string | null;
}

export function useAccountMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: KEY });
  return {
    create: useMutation({
      mutationFn: (body: { email: string; role: Role; teacher_id: number | null; full_name?: string | null }) =>
        api<CreatedAccount>('POST', '/api/admin/accounts', body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: number; body: Partial<Pick<Account, 'email' | 'role' | 'active' | 'full_name'>> }) =>
        api<Account>('PATCH', `/api/admin/accounts/${id}`, body),
      onSuccess: invalidate,
    }),
    // Отключить/включить учётку (обратимо).
    setActive: useMutation({
      mutationFn: ({ id, active }: { id: number; active: boolean }) =>
        api<{ ok: true; active: boolean }>('POST', `/api/admin/accounts/${id}/set-active`, { active }),
      onSuccess: invalidate,
    }),
    // reset-password теперь выписывает invite-ссылку (не temp-пароль).
    resetPassword: useMutation({
      mutationFn: (id: number) => api<InviteResult>('POST', `/api/admin/accounts/${id}/reset-password`),
      onSuccess: invalidate,
    }),
    // Перевыписать invite-ссылку (revoke старых + новая).
    invite: useMutation({
      mutationFn: (id: number) => api<InviteResult>('POST', `/api/admin/accounts/${id}/invite`),
      onSuccess: invalidate,
    }),
    // Отозвать активную invite-ссылку.
    revokeInvite: useMutation({
      mutationFn: (id: number) => api<{ ok: true }>('POST', `/api/admin/accounts/${id}/invite/revoke`),
      onSuccess: invalidate,
    }),
    reset2fa: useMutation({
      mutationFn: (id: number) => api<{ ok: true }>('POST', `/api/admin/accounts/${id}/reset-2fa`),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `/api/admin/accounts/${id}`),
      onSuccess: invalidate,
    }),
  };
}
