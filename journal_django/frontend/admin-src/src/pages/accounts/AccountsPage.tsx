import { useState, type FormEvent } from 'react';
import { useAccounts, useAccountMutations, type CreatedAccount, type InviteResult } from '../../hooks/useAccounts';
import { useTeachers } from '../../hooks/useTeachers';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { SelectInput } from '../../components/form/SelectInput';
import { Combobox } from '../../components/form/Combobox';
import { DataTable, type Column } from '../../components/table/DataTable';
import { ActionMenu, type ActionMenuItem } from '../../components/ui/ActionMenu';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { TableSkeleton } from '../../components/ui/Skeleton';
import type { Account, AccountStatus, Role } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';

// ─── Подписи ролей ────────────────────────────────────────────────────────────

const ROLE_LABELS: Record<Role, string> = {
  teacher:    'Преподаватель',
  manager:    'Менеджер',
  admin:      'Администратор',
  superadmin: 'Суперадминистратор',
};

const ROLE_OPTIONS: { value: Role; label: string }[] = [
  { value: 'teacher',    label: 'Преподаватель' },
  { value: 'manager',    label: 'Менеджер' },
  { value: 'admin',      label: 'Администратор' },
  { value: 'superadmin', label: 'Суперадминистратор' },
];

// ─── Вспомогалки ──────────────────────────────────────────────────────────────

function buildQuery(
  page: number,
  pageSize: number,
  sortBy: string,
  sortDir: 'asc' | 'desc',
  filters: Record<string, string>,
): string {
  const p = new URLSearchParams();
  p.set('page', String(page));
  p.set('page_size', String(pageSize));
  p.set('sort_by', sortBy);
  p.set('sort_dir', sortDir);
  for (const [k, v] of Object.entries(filters)) {
    if (v) p.set(`filter[${k}]`, v);
  }
  return '?' + p.toString();
}

// ─── Модалка «Создать учётку» ─────────────────────────────────────────────────

interface CreateModalProps {
  teacherOptions: { value: string; label: string }[];
  onClose: () => void;
  onCreated: (acc: CreatedAccount) => void;
}

function CreateAccountModal({ teacherOptions, onClose, onCreated }: CreateModalProps) {
  const muts = useAccountMutations();
  const showError = useApiError();

  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('teacher');
  const [teacherId, setTeacherId] = useState('');
  const [fullName, setFullName] = useState('');
  const [errors, setErrors] = useState<{ email?: string; teacher?: string }>({});

  const validate = (): boolean => {
    const e: { email?: string; teacher?: string } = {};
    if (!email.trim()) e.email = 'Введите email';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) e.email = 'Некорректный email';
    if (role === 'teacher' && !teacherId) e.teacher = 'Выберите преподавателя';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    try {
      const result = await muts.create.mutateAsync({
        email: email.trim(),
        role,
        teacher_id: role === 'teacher' ? Number(teacherId) : null,
        full_name: role !== 'teacher' && fullName.trim() ? fullName.trim() : undefined,
      });
      onCreated(result);
    } catch (err) {
      showError(err);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Новая учётка"
      footer={
        <button
          type="submit"
          form="account-create-form"
          className="btn-add"
          disabled={muts.create.isPending}
        >
          {muts.create.isPending ? 'Создаём…' : 'Создать'}
        </button>
      }
    >
      <form id="account-create-form" onSubmit={onSubmit}>
        <Field label="Email" required error={errors.email}>
          <TextInput
            type="email"
            value={email}
            onChange={(e) => { setEmail(e.target.value); setErrors((p) => ({ ...p, email: undefined })); }}
            placeholder="teacher@example.com"
          />
        </Field>

        <Field label="Роль" required>
          <SelectInput
            value={role}
            onChange={(e) => {
              setRole(e.target.value as Role);
              if (e.target.value !== 'teacher') setTeacherId('');
              else setFullName('');
              setErrors((p) => ({ ...p, teacher: undefined }));
            }}
            options={ROLE_OPTIONS}
          />
        </Field>

        {role === 'teacher' ? (
          <Field label="Преподаватель" required error={errors.teacher}>
            <Combobox
              value={teacherId}
              onChange={(v) => { setTeacherId(v); setErrors((p) => ({ ...p, teacher: undefined })); }}
              options={teacherOptions}
              placeholder="Начните вводить имя…"
            />
          </Field>
        ) : (
          <Field label="Имя">
            <TextInput
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Иван Иванов"
            />
          </Field>
        )}
      </form>
    </Dialog>
  );
}

// ─── Модалка «Изменить имя» ────────────────────────────────────────────────────

interface EditNameModalProps {
  account: Account;
  onClose: () => void;
}

function EditNameModal({ account, onClose }: EditNameModalProps) {
  const muts = useAccountMutations();
  const showError = useApiError();
  const [fullName, setFullName] = useState(account.full_name ?? '');

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await muts.update.mutateAsync({ id: account.id, body: { full_name: fullName.trim() || null } });
      onClose();
    } catch (err) {
      showError(err);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Изменить имя"
      footer={
        <button
          type="submit"
          form="account-edit-name-form"
          className="btn-add"
          disabled={muts.update.isPending}
        >
          {muts.update.isPending ? 'Сохраняем…' : 'Сохранить'}
        </button>
      }
    >
      <form id="account-edit-name-form" onSubmit={onSubmit}>
        <Field label="Имя">
          <TextInput
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Иван Иванов"
          />
        </Field>
      </form>
    </Dialog>
  );
}

// ─── Модалка «Показать invite-ссылку» ─────────────────────────────────────────

interface InviteRevealModalProps {
  title: string;
  email: string;
  inviteUrl: string;
  expiresAt: string;
  onClose: () => void;
}

function InviteRevealModal({ title, email, inviteUrl, expiresAt, onClose }: InviteRevealModalProps) {
  const { toast } = useToast();

  // invite_url приходит относительным ('/login/set-password?token=…') — собираем абсолютный
  // для передачи сотруднику.
  const fullUrl = inviteUrl.startsWith('http') ? inviteUrl : `${window.location.origin}${inviteUrl}`;

  const copy = () => {
    navigator.clipboard.writeText(fullUrl).then(
      () => toast('Ссылка скопирована', 'ok'),
      () => toast('Не удалось скопировать', 'error'),
    );
  };

  const expires = new Date(expiresAt);
  const expiresText = Number.isNaN(expires.getTime())
    ? expiresAt
    : expires.toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={title}
      footer={
        <button type="button" className="btn-add" onClick={onClose}>
          Понятно, закрыть
        </button>
      }
    >
      <div className="password-reveal">
        <p className="password-reveal__hint">
          Передайте эту ссылку сотруднику <strong>лично или в мессенджере</strong>. По ней он сам
          задаст пароль и настроит 2FA. Ссылка одноразовая и действует до <strong>{expiresText}</strong>.
        </p>
        <div className="password-reveal__email">{email}</div>
        <div className="password-reveal__box">
          <code className="password-reveal__value">{fullUrl}</code>
          <button
            type="button"
            className="btn-link password-reveal__copy"
            onClick={copy}
            title="Скопировать ссылку"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            Скопировать
          </button>
        </div>
      </div>
    </Dialog>
  );
}

// ─── Тип «ожидаемого подтверждения» ──────────────────────────────────────────

type PendingAction =
  | { type: 'resetPassword'; accountId: number; email: string }
  | { type: 'regenerate';    accountId: number; email: string }
  | { type: 'revokeInvite';  accountId: number; email: string }
  | { type: 'reset2fa';      accountId: number; email: string }
  | { type: 'disable';       accountId: number; email: string }
  | { type: 'enable';        accountId: number; email: string }
  | { type: 'delete';        accountId: number; email: string };

// ─── Статус учётки ─────────────────────────────────────────────────────────────

const STATUS_BADGE: Record<AccountStatus, { cls: string; label: string }> = {
  active:   { cls: 'badge--ok',       label: 'Активна' },
  invited:  { cls: 'badge--info',     label: 'Приглашён' },
  expired:  { cls: 'badge--muted',    label: 'Ссылка истекла' },
  disabled: { cls: 'badge--inactive', label: 'Выключена' },
};

// ─── Главный компонент ────────────────────────────────────────────────────────

export default function AccountsPage() {
  const { page, pageSize, sortBy, sortDir, filters, setPage, setPageSize, setSort, setFilters } =
    useListSearchParams({ sortBy: 'email', sortDir: 'asc' });

  const query = buildQuery(page, pageSize, sortBy, sortDir, filters);
  const { data, isLoading, isFetching } = useAccounts(query);

  const { data: teachersRaw = [] } = useTeachers(false);
  const teacherOptions = teachersRaw.map((t) => ({ value: String(t.id), label: t.name }));

  const muts = useAccountMutations();
  const { toast } = useToast();
  const showError = useApiError();

  // Модалки
  const [createOpen, setCreateOpen]     = useState(false);
  const [editingAccount, setEditingAccount] = useState<Account | null>(null);
  const [revealData, setRevealData]     = useState<{ title: string; email: string; inviteUrl: string; expiresAt: string } | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);

  // ── Обработчики ──────────────────────────────────────────────────────────────

  const handleCreated = (acc: CreatedAccount) => {
    setCreateOpen(false);
    setRevealData({ title: 'Учётка создана', email: acc.email, inviteUrl: acc.invite_url, expiresAt: acc.expires_at });
  };

  const revealInvite = (title: string, email: string, res: InviteResult) => {
    setRevealData({ title, email, inviteUrl: res.invite_url, expiresAt: res.expires_at });
  };

  const handleConfirmAction = async () => {
    if (!pendingAction) return;
    try {
      if (pendingAction.type === 'resetPassword') {
        const result = await muts.resetPassword.mutateAsync(pendingAction.accountId);
        setPendingAction(null);
        revealInvite('Ссылка для смены пароля', pendingAction.email, result);
      } else if (pendingAction.type === 'regenerate') {
        const result = await muts.invite.mutateAsync(pendingAction.accountId);
        setPendingAction(null);
        revealInvite('Новая invite-ссылка', pendingAction.email, result);
      } else if (pendingAction.type === 'revokeInvite') {
        await muts.revokeInvite.mutateAsync(pendingAction.accountId);
        toast('Ссылка отозвана', 'ok');
        setPendingAction(null);
      } else if (pendingAction.type === 'reset2fa') {
        await muts.reset2fa.mutateAsync(pendingAction.accountId);
        toast('2FA сброшена', 'ok');
        setPendingAction(null);
      } else if (pendingAction.type === 'disable') {
        await muts.setActive.mutateAsync({ id: pendingAction.accountId, active: false });
        toast('Учётка отключена', 'ok');
        setPendingAction(null);
      } else if (pendingAction.type === 'enable') {
        await muts.setActive.mutateAsync({ id: pendingAction.accountId, active: true });
        toast('Учётка включена', 'ok');
        setPendingAction(null);
      } else if (pendingAction.type === 'delete') {
        await muts.remove.mutateAsync(pendingAction.accountId);
        toast('Учётка удалена', 'ok');
        setPendingAction(null);
      }
    } catch (err) {
      showError(err);
      setPendingAction(null);
    }
  };

  const isConfirmPending =
    muts.resetPassword.isPending || muts.invite.isPending || muts.revokeInvite.isPending ||
    muts.reset2fa.isPending || muts.setActive.isPending || muts.remove.isPending;

  const getConfirmConfig = (action: PendingAction): { title: string; message: string; confirmLabel: string; danger?: boolean } => {
    switch (action.type) {
      case 'resetPassword':
        return {
          title: 'Сбросить пароль?',
          message: `Для ${action.email} будет выписана новая invite-ссылка для установки пароля. Текущий пароль и активные сессии перестанут работать.`,
          confirmLabel: 'Сбросить пароль',
        };
      case 'regenerate':
        return {
          title: 'Перевыписать ссылку?',
          message: `Прежняя ссылка для ${action.email} будет отозвана, и создана новая (действует 48 часов).`,
          confirmLabel: 'Перевыписать',
        };
      case 'revokeInvite':
        return {
          title: 'Отозвать ссылку?',
          message: `Активная invite-ссылка для ${action.email} перестанет работать. Чтобы дать доступ снова, выпишите новую.`,
          confirmLabel: 'Отозвать',
          danger: true,
        };
      case 'reset2fa':
        return {
          title: 'Сбросить 2FA?',
          message: `2FA для ${action.email} будет сброшена. Пользователю придётся настроить заново.`,
          confirmLabel: 'Сбросить 2FA',
        };
      case 'disable':
        return {
          title: 'Отключить учётку?',
          message: `Учётка ${action.email} будет отключена и разлогинена. Доступ можно вернуть в любой момент кнопкой «Включить».`,
          confirmLabel: 'Отключить',
          danger: true,
        };
      case 'enable':
        return {
          title: 'Включить учётку?',
          message: `Учётка ${action.email} снова получит доступ к системе.`,
          confirmLabel: 'Включить',
        };
      case 'delete':
        return {
          title: 'Удалить учётку безвозвратно?',
          message: `Учётка ${action.email} будет удалена физически — это действие НЕЛЬЗЯ отменить. История аудита сохранится, но саму учётку восстановить будет невозможно.`,
          confirmLabel: 'Удалить навсегда',
          danger: true,
        };
    }
  };

  // ── Колонки ──────────────────────────────────────────────────────────────────

  const columns: Column<Account>[] = [
    {
      key: 'name',
      label: 'Имя',
      sortable: false,
      searchable: false,
      cell: (r) => r.name || r.full_name || r.teacher_name || <span style={{ color: 'var(--text3)' }}>—</span>,
    },
    {
      key: 'email',
      label: 'Email',
      sortable: true,
      searchable: true,
      cell: (r) => <span className="mono">{r.email}</span>,
    },
    {
      key: 'role',
      label: 'Роль',
      sortable: true,
      searchable: true,
      searchOptions: ROLE_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
      cell: (r) => ROLE_LABELS[r.role] ?? r.role,
    },
    {
      key: 'teacher_name',
      label: 'Преподаватель',
      searchable: true,
      cell: (r) => r.teacher_name || <span style={{ color: 'var(--text3)' }}>—</span>,
    },
    {
      key: 'twofa_method',
      label: '2FA',
      sortable: false,
      cell: (r) => {
        if (!r.twofa_enabled || !r.twofa_method) return <span style={{ color: 'var(--text3)' }}>—</span>;
        const label = r.twofa_method === 'totp' ? 'TOTP' : 'Email';
        return <span className="badge badge--ok">{label}</span>;
      },
    },
    {
      key: 'active',
      label: 'Статус',
      sortable: true,
      searchable: true,
      searchOptions: [
        { value: 'true',  label: 'Активна' },
        { value: 'false', label: 'Выключена' },
      ],
      cell: (r) => {
        // status вычисляется сервером (invited/active/expired/disabled); fallback по active.
        const status: AccountStatus = r.status ?? (r.active ? 'active' : 'disabled');
        const b = STATUS_BADGE[status];
        return <span className={`badge ${b.cls}`}>{b.label}</span>;
      },
    },
    {
      key: '_actions',
      label: '',
      sortable: false,
      cell: (r) => {
        const status: AccountStatus = r.status ?? (r.active ? 'active' : 'disabled');

        // Пять-шесть равновесных действий в ряд ломались по словам в узкой
        // колонке; сворачиваем их в одно меню «…» — порядок от частого к
        // опасному, «Удалить» помечено danger и стоит последним.
        const items: ActionMenuItem[] = [];

        if (status === 'invited' || status === 'expired') {
          items.push({ label: 'Новая ссылка', onSelect: () => setPendingAction({ type: 'regenerate', accountId: r.id, email: r.email }) });
        } else if (status === 'active') {
          items.push({ label: 'Сброс пароля', onSelect: () => setPendingAction({ type: 'resetPassword', accountId: r.id, email: r.email }) });
        }

        if (r.has_active_invite) {
          items.push({ label: 'Отозвать ссылку', onSelect: () => setPendingAction({ type: 'revokeInvite', accountId: r.id, email: r.email }) });
        }

        if (r.twofa_enabled) {
          items.push({ label: 'Сброс 2FA', onSelect: () => setPendingAction({ type: 'reset2fa', accountId: r.id, email: r.email }) });
        }

        if (r.role !== 'teacher') {
          items.push({ label: 'Изменить имя', onSelect: () => setEditingAccount(r) });
        }

        items.push(
          r.active
            ? { label: 'Отключить', onSelect: () => setPendingAction({ type: 'disable', accountId: r.id, email: r.email }) }
            : { label: 'Включить', onSelect: () => setPendingAction({ type: 'enable', accountId: r.id, email: r.email }) },
        );

        items.push({ label: 'Удалить', danger: true, onSelect: () => setPendingAction({ type: 'delete', accountId: r.id, email: r.email }) });

        return (
          <div
            style={{ display: 'flex', justifyContent: 'flex-end' }}
            onClick={(e) => e.stopPropagation()}
          >
            <ActionMenu items={items} label="Действия с учёткой" />
          </div>
        );
      },
    },
  ];

  // ── Render ───────────────────────────────────────────────────────────────────

  const rows     = data?.rows ?? [];
  const total    = data?.total ?? 0;

  // Шапка рисуется и во время загрузки — иначе заголовок пропадает при переходе.
  const header = (
    <PageHeader
      title="Учётные записи"
      count={isLoading ? undefined : total}
      sub="Доступ в систему: роли, приглашения и отключение учёток."
      actions={
        <button type="button" className="btn-add" onClick={() => setCreateOpen(true)}>
          + Новая учётка
        </button>
      }
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={8} cols={7} /></>;

  return (
    <>
      {header}
      <DataTable<Account>
        data={rows}
        columns={columns}
        title="Учётные записи"
        isLoading={isFetching}
        serverPagination={{
          page,
          pageSize,
          total,
          sortBy,
          sortDir,
          filters,
          onPageChange:    setPage,
          onPageSizeChange: setPageSize,
          onSortChange:    setSort,
          onFiltersChange: setFilters,
        }}
      />

      {/* Создать учётку */}
      {createOpen && (
        <CreateAccountModal
          teacherOptions={teacherOptions}
          onClose={() => setCreateOpen(false)}
          onCreated={handleCreated}
        />
      )}

      {/* Показать выданную invite-ссылку (один раз) */}
      {revealData && (
        <InviteRevealModal
          title={revealData.title}
          email={revealData.email}
          inviteUrl={revealData.inviteUrl}
          expiresAt={revealData.expiresAt}
          onClose={() => setRevealData(null)}
        />
      )}

      {/* Изменить имя */}
      {editingAccount && (
        <EditNameModal
          account={editingAccount}
          onClose={() => setEditingAccount(null)}
        />
      )}

      {/* Подтверждение опасного действия */}
      {pendingAction && (
        <ConfirmModal
          {...getConfirmConfig(pendingAction)}
          isPending={isConfirmPending}
          onConfirm={() => void handleConfirmAction()}
          onClose={() => setPendingAction(null)}
        />
      )}
    </>
  );
}
