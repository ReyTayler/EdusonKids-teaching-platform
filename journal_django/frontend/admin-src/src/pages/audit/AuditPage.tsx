import { useAudit } from '../../hooks/useAudit';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { DataTable, type Column } from '../../components/table/DataTable';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { fmtDateTime } from '../../lib/format';
import type { AuditEntry } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';

// ─── Подписи событий ──────────────────────────────────────────────────────────

// Журнал ИБ фиксирует только события безопасности (вход/2FA/учётки/инвайты) и
// массовые операции с данными. Доменные действия (уроки, план, доп.уроки) сюда
// НЕ пишутся — они в «Журнале изменений» (pghistory).
const EVENT_LABELS: Record<string, string> = {
  login_success:       'Вход',
  login_fail:          'Неуспешный вход',
  logout:              'Выход',
  '2fa_fail':          'Ошибка 2FA',
  '2fa_enabled':       '2FA включена',
  '2fa_disabled':      '2FA выключена',
  '2fa_reset':         'Сброс 2FA',
  account_created:     'Учётка создана',
  password_reset:      'Сброс пароля',
  account_deactivated: 'Учётка выключена',
  account_disabled:    'Учётка отключена',
  account_enabled:     'Учётка включена',
  account_deleted:     'Учётка удалена',
  locked:              'Блокировка',
  invite_created:      'Инвайт выдан',
  invite_revoked:      'Инвайт отозван',
  invite_used:         'Инвайт активирован',
  invite_accept_fail:  'Ошибка активации инвайта',
  // Массовые операции с данными: импорт из Sheets и выгрузка отчёта — точки
  // входа/выхода персональных данных, поэтому остаются в журнале ИБ.
  'sync.run':          'Запуск синхронизации',
  'report.run':        'Выгрузка отчёта',
};

const EVENT_OPTIONS = Object.entries(EVENT_LABELS).map(([value, label]) => ({ value, label }));

// ─── Вспомагалка buildQuery (повторяет паттерн AccountsPage) ─────────────────

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

// ─── Главный компонент ────────────────────────────────────────────────────────

export default function AuditPage() {
  const { page, pageSize, sortBy, sortDir, filters, setPage, setPageSize, setSort, setFilters } =
    useListSearchParams({ sortBy: 'occurred_at', sortDir: 'desc' });

  const query = buildQuery(page, pageSize, sortBy, sortDir, filters);
  const { data, isLoading, isFetching } = useAudit(query);

  const columns: Column<AuditEntry>[] = [
    {
      key: 'occurred_at',
      label: 'Время',
      sortable: true,
      width: '13rem',
      cell: (r) => (
        <span className="mono" style={{ color: 'var(--text2)', fontSize: '0.8125rem' }}>
          {fmtDateTime(r.occurred_at)}
        </span>
      ),
    },
    {
      key: 'event',
      label: 'Событие',
      sortable: true,
      searchable: true,
      searchOptions: EVENT_OPTIONS,
      cell: (r) => {
        const label = EVENT_LABELS[r.event] ?? r.event;
        const isFailure =
          r.event === 'login_fail' ||
          r.event === '2fa_fail'   ||
          r.event === 'locked'     ||
          r.event === 'invite_accept_fail'  ||
          r.event === 'account_deactivated' ||
          r.event === 'account_disabled'    ||
          r.event === 'account_deleted'     ||
          r.event === 'invite_revoked';
        const isSecurity =
          r.event.startsWith('2fa')    ||
          r.event.startsWith('invite') ||
          r.event.startsWith('sync.')  ||
          r.event.startsWith('report.') ||
          r.event === 'password_reset' ||
          r.event === 'account_created';
        if (isFailure) {
          return <span className="status-badge status-badge--negative">{label}</span>;
        }
        if (isSecurity) {
          return <span className="status-badge status-badge--info">{label}</span>;
        }
        // login_success / logout — нейтральный текст без бейджа
        return <span style={{ color: 'var(--text2)' }}>{label}</span>;
      },
    },
    {
      key: 'account_email',
      label: 'Учётка',
      searchable: true,
      cell: (r) => {
        const email = r.account_email || r.actor_email;
        if (!email) return <span style={{ color: 'var(--text3)' }}>—</span>;
        return <span className="mono">{email}</span>;
      },
    },
    {
      key: 'ip',
      label: 'IP',
      sortable: false,
      cell: (r) =>
        r.ip
          ? <span className="mono" style={{ color: 'var(--text2)' }}>{r.ip}</span>
          : <span style={{ color: 'var(--text3)' }}>—</span>,
    },
  ];

  const rows  = data?.rows  ?? [];
  const total = data?.total ?? 0;

  // Шапка рисуется и во время загрузки — иначе заголовок пропадает
  // при каждом переходе в раздел.
  const header = (
    <PageHeader title="Журнал событий" count={isLoading ? undefined : total} />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={12} cols={4} /></>;

  return (
    <>
      {header}
      <DataTable<AuditEntry>
      data={rows}
      columns={columns}
      title="Журнал событий"
      isLoading={isFetching}
      serverPagination={{
        page,
        pageSize,
        total,
        sortBy,
        sortDir,
        filters,
        onPageChange:     setPage,
        onPageSizeChange: setPageSize,
        onSortChange:     setSort,
        onFiltersChange:  setFilters,
      }}
      />
    </>
  );
}
