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
      key: 'lesson_seq', label: CANCELLATION_LABELS.colLesson, sortable: false,
      cell: (r) => (
        <div className="cell-stack">
          <span className="cell-stack__main">
            {r.lesson_seq != null ? CANCELLATION_LABELS.lessonNumber(r.lesson_seq) : '—'}
          </span>
          {r.teacher_name && <span className="cell-stack__sub">{r.teacher_name}</span>}
        </div>
      ),
    },
    {
      key: 'lesson_date', label: CANCELLATION_LABELS.colScheduledAt, sortable: false,
      wrapHeader: true, width: 190,
      cell: (r) => <span className="num">{fmtDate(r.lesson_date)} {r.scheduled_time}</span>,
    },
    {
      key: 'side', label: CANCELLATION_LABELS.colSide, sortable: false,
      cell: (r) => CANCELLATION_SIDE_LABELS[r.side],
    },
    {
      key: 'reason_text', label: CANCELLATION_LABELS.colReason, sortable: false,
      cell: (r) => (r.is_other ? CANCELLATION_LABELS.otherReason : r.reason_text),
    },
    {
      key: 'details', label: CANCELLATION_LABELS.colDetails, sortable: false,
      cell: (r) => {
        // У старых записей с «Другой причиной» подробности лежат в reason_text.
        const text = r.is_other ? r.reason_text : r.details;
        return text ? <ClampText text={text} className="clamp-text--column" /> : '—';
      },
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
    {
      key: 'notified_on', label: CANCELLATION_LABELS.notifiedOn, sortable: false,
      cell: (r) => (r.notified_on ? <span className="num">{fmtDate(r.notified_on)}</span> : '—'),
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
