import { useEffect, useState } from 'react';
import { useChangelogList } from '../../hooks/useChangelog';
import { DataTable, type Column } from '../table/DataTable';
import { TableSkeleton } from '../ui/Skeleton';
import { ChangelogDetailModal } from '../../pages/changelog/ChangelogDetailModal';
import { CHANGELOG_OPERATION_LABELS } from '../../lib/labels';
import { TimeCell, ActorCell, OperationCell } from './columnRenderers';
import type { ChangelogOperation } from '../../lib/types';

const PAGE_SIZE = 15;

function buildQuery(page: number, entity: string, entityId: number): string {
  const p = new URLSearchParams();
  p.set('page', String(page));
  p.set('page_size', String(PAGE_SIZE));
  p.set('filter[entity]', entity);
  p.set('filter[entity_id]', String(entityId));
  return '?' + p.toString();
}

/** Компактная read-only лента изменений одной сущности — для вкладок «История». */
export function EntityChangelogPanel({ entity, entityId }: { entity: string; entityId: number }) {
  const [page, setPage] = useState(1);
  const [openedId, setOpenedId] = useState<string | null>(null);

  // Панель остаётся смонтированной при навигации между сущностями (Tabs
  // ключует панель по value таба «history», не по entityId) — сбрасываем
  // страницу на 1, иначе на новой сущности залипнет не-первая страница.
  useEffect(() => {
    setPage(1);
    setOpenedId(null);
  }, [entity, entityId]);

  const query = buildQuery(page, entity, entityId);
  const { data, isLoading, isFetching } = useChangelogList(query);

  const rows  = data?.rows  ?? [];
  const total = data?.total ?? 0;

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
  ];

  if (isLoading) return <TableSkeleton rows={6} cols={4} />;

  return (
    <>
      <DataTable<ChangelogOperation>
        data={rows}
        columns={columns}
        title="История изменений"
        isLoading={isFetching}
        onRowClick={(r) => setOpenedId(r.id)}
        serverPagination={{
          page,
          pageSize: PAGE_SIZE,
          total,
          sortBy: 'occurred_at',
          sortDir: 'desc',
          filters: {},
          onPageChange: setPage,
          onPageSizeChange: () => {},
          onSortChange: () => {},
          onFiltersChange: () => {},
        }}
      />

      {openedId && (
        <ChangelogDetailModal
          contextId={openedId}
          onClose={() => setOpenedId(null)}
          readOnly
        />
      )}
    </>
  );
}
