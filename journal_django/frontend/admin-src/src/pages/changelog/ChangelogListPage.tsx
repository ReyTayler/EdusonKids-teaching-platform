import { useState } from 'react';
import { useChangelogList } from '../../hooks/useChangelog';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { useAuth } from '../../hooks/useAuth';
import { canRevertChangelog, type Role } from '../../lib/permissions';
import { DataTable, type Column } from '../../components/table/DataTable';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { SelectInput } from '../../components/form/SelectInput';
import { ChangelogDetailModal } from './ChangelogDetailModal';
import { RevertConfirmDialog } from './RevertConfirmDialog';
import { ACTION_ICONS, TimeCell, ActorCell, OperationCell } from '../../components/changelog/columnRenderers';
import {
  CHANGELOG_ENTITY_LABELS,
  CHANGELOG_OPERATION_LABELS,
  CHANGELOG_OPERATION_OPTIONS,
} from '../../lib/labels';
import type { ChangelogOperation } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';

// ─── buildQuery: сортировка ленты фиксирована на бэке (occurred_at DESC) ──────

function buildQuery(page: number, pageSize: number, filters: Record<string, string>): string {
  const p = new URLSearchParams();
  p.set('page', String(page));
  p.set('page_size', String(pageSize));
  for (const [k, v] of Object.entries(filters)) {
    if (v) p.set(`filter[${k}]`, v);
  }
  return '?' + p.toString();
}

// ─── Главный компонент ────────────────────────────────────────────────────────

export default function ChangelogListPage() {
  const { me } = useAuth();
  const {
    page, pageSize, sortBy, sortDir, filters,
    setPage, setPageSize, setSort, setFilters, getExtra, setExtras,
  } = useListSearchParams({ sortBy: 'occurred_at', sortDir: 'desc' });

  // Фильтр «история записи» приходит по deep-link из конфликта отката.
  const entity   = getExtra('entity')   ?? '';
  const entityId = getExtra('entity_id') ?? '';
  const operation = getExtra('op') ?? '';

  const [openedId, setOpenedId] = useState<string | null>(null);
  const [reverting, setReverting] = useState<ChangelogOperation | null>(null);

  const query = buildQuery(page, pageSize, {
    ...filters,
    operation,
    entity,
    entity_id: entity ? entityId : '',
  });
  const { data, isLoading, isFetching } = useChangelogList(query);

  const columns: Column<ChangelogOperation>[] = [
    {
      key: 'occurred_at',
      label: 'Время',
      width: '7rem',
      cell: (r) => <TimeCell occurredAt={r.occurred_at} />,
    },
    {
      key: 'operation',
      label: 'Действие',
      width: '14rem',
      cell: (r) => (
        <OperationCell operation={r.operation} label={CHANGELOG_OPERATION_LABELS[r.operation] ?? r.operation} />
      ),
    },
    {
      key: 'summary',
      label: 'Описание',
      cell: (r) => <span>{r.summary}</span>,
    },
    {
      key: 'actor',
      label: 'Кто',
      width: '13rem',
      cell: (r) => <ActorCell actor={r.actor} />,
    },
    {
      key: 'status',
      label: 'Статус',
      width: '8rem',
      cell: (r) =>
        r.reverted
          ? <span className="status-badge status-badge--muted">откачено</span>
          : r.operation === 'changelog.revert'
            ? <span className="status-badge status-badge--info">откат</span>
            : <span className="status-badge status-badge--positive">применено</span>,
    },
    {
      key: 'revert',
      label: '',
      width: '8rem',
      cell: (r) =>
        canRevertChangelog(me?.role as Role) && r.revertable ? (
          <button
            type="button"
            className="btn-cancel"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)', whiteSpace: 'nowrap' }}
            onClick={(e) => { e.stopPropagation(); setReverting(r); }}
          >
            {ACTION_ICONS.revert} Откатить
          </button>
        ) : null,
    },
  ];

  const rows  = data?.rows  ?? [];
  const total = data?.total ?? 0;

  const entityFilterLabel = entity
    ? `${CHANGELOG_ENTITY_LABELS[entity] ?? entity}${entityId ? ` #${entityId}` : ''}`
    : null;

  // Шапка рисуется и во время загрузки — иначе заголовок пропадает при переходе.
  // Пояснение «все правки сохраняются…» переехало из ряда действий в подзаголовок:
  // это описание раздела, а не элемент управления.
  const header = (
    <PageHeader
      title="Журнал изменений"
      count={isLoading ? undefined : total}
      sub="Все правки сохраняются, любую операцию можно откатить."
      actions={
        <>
          <div className="changelog-op-filter">
            <SelectInput
              options={[{ value: '', label: 'Все действия' }, ...CHANGELOG_OPERATION_OPTIONS]}
              value={operation}
              onChange={(e) => setExtras({ op: e.target.value || null })}
            />
          </div>
          {entityFilterLabel && (
            <span className="filter-chip">
              {entityFilterLabel}
              <button
                type="button"
                className="filter-chip__clear"
                aria-label="Сбросить фильтр записи"
                onClick={() => setExtras({ entity: null, entity_id: null })}
              >×</button>
            </span>
          )}
        </>
      }
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={12} cols={6} /></>;

  return (
    <>
      {header}
      <DataTable<ChangelogOperation>
        data={rows}
        columns={columns}
        title="Журнал изменений"
        isLoading={isFetching}
        onRowClick={(r) => setOpenedId(r.id)}
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

      {openedId && (
        <ChangelogDetailModal
          contextId={openedId}
          onClose={() => setOpenedId(null)}
          onRevert={(op) => { setOpenedId(null); setReverting(op); }}
        />
      )}
      {reverting && (
        <RevertConfirmDialog op={reverting} onClose={() => setReverting(null)} />
      )}
    </>
  );
}
