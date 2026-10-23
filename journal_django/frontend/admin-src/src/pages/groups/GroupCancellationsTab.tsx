import { useGroupCancellations } from '../../hooks/useGroupCancellations';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { DataTable, type Column } from '../../components/table/DataTable';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { EmptyState } from '../../components/ui/EmptyState';
import { ClampText } from '../../components/ui/ClampText';
import { fmtDate, fmtDateTime } from '../../lib/format';
import { CANCELLATION_LABELS, CANCELLATION_SIDE_LABELS } from '../../lib/labels';
import type { LessonCancellationRow } from '../../lib/types';

/**
 * Вкладка «Отмены уроков» карточки группы: журнал отмен (только чтение).
 * Колонки компенсации нет намеренно — решение о деньгах преподавателя
 * конфиденциально и принимается в справочнике причин.
 */
export function GroupCancellationsTab({ groupId }: { groupId: number }) {
  const { page, pageSize, setPage, setPageSize } = useListSearchParams({ sortBy: 'lesson_date', sortDir: 'desc' });
  const { data, isLoading, isFetching, isError, refetch } = useGroupCancellations(groupId, {
    page, page_size: pageSize,
  });
  const rows: LessonCancellationRow[] = data?.rows ?? [];
  const total = data?.total ?? 0;

  const columns: Column<LessonCancellationRow>[] = [
    {
      key: 'lesson_date', label: CANCELLATION_LABELS.colLessonDate, sortable: false,
      cell: (r) => (
        <div className="cell-stack">
          <span className="cell-stack__main num">{fmtDate(r.lesson_date)} {r.scheduled_time}</span>
          {r.teacher_name && <span className="cell-stack__sub">{r.teacher_name}</span>}
        </div>
      ),
    },
    {
      key: 'side', label: CANCELLATION_LABELS.colSide, sortable: false,
      cell: (r) => CANCELLATION_SIDE_LABELS[r.side],
    },
    {
      key: 'reason_text', label: CANCELLATION_LABELS.colReason, sortable: false,
      // Для «Другой причины» reason_text — сам текст, возможно длинный.
      cell: (r) => (
        <div className="cell-stack">
          {r.is_other && <span className="cell-stack__sub">{CANCELLATION_LABELS.otherReason}</span>}
          <ClampText text={r.reason_text} className="clamp-text--column" />
        </div>
      ),
    },
    {
      key: 'created_by_name', label: CANCELLATION_LABELS.colCancelledBy, sortable: false,
      cell: (r) => (
        <div className="cell-stack">
          <span className="cell-stack__main">{r.created_by_name ?? '—'}</span>
          <span className="cell-stack__sub num">{fmtDateTime(r.created_at)}</span>
        </div>
      ),
    },
  ];

  if (isError && !data) {
    // Упавший запрос не должен выглядеть как «отмен не было».
    return (
      <EmptyState
        hint={CANCELLATION_LABELS.errorHint}
        action={<button type="button" className="btn-cancel" onClick={() => { void refetch(); }}>{CANCELLATION_LABELS.retry}</button>}
      >
        {CANCELLATION_LABELS.errorTitle}
      </EmptyState>
    );
  }
  if (isLoading) return <TableSkeleton rows={6} cols={columns.length} />;
  if (total === 0) return <EmptyState>{CANCELLATION_LABELS.empty}</EmptyState>;

  return (
    <DataTable<LessonCancellationRow>
      data={rows}
      columns={columns}
      title={CANCELLATION_LABELS.title}
      rowKey={(r) => r.id}
      isLoading={isFetching}
      serverPagination={{
        page, pageSize, total,
        sortBy: 'lesson_date', sortDir: 'desc', filters: {},
        onPageChange: setPage,
        onPageSizeChange: setPageSize,
        onSortChange: () => {},
        onFiltersChange: () => {},
      }}
    />
  );
}
