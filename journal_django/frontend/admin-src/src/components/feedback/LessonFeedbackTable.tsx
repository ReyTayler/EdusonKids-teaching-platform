import { useDeferredValue, useMemo } from 'react';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { useLessonFeedback } from '../../hooks/useLessonFeedback';
import { useGroupsAll } from '../../hooks/useGroups';
import { useTeachers } from '../../hooks/useTeachers';
import { DataTable, type Column } from '../table/DataTable';
import { TableSkeleton } from '../ui/Skeleton';
import { EmptyState } from '../ui/EmptyState';
import { ClampText } from '../ui/ClampText';
import { EntityLink } from '../EntityLink';
import { DateInput } from '../form/DateInput';
import { fmtDate, fmtDateTime } from '../../lib/format';
import { FEEDBACK_LABELS } from '../../lib/labels';
import type { LessonFeedbackRow } from '../../lib/types';

/** Оценка 1–10; null (урок не состоялся) — прочерк, а не пустое « / 10». */
function ScoreCell({ value }: { value: number | null }) {
  if (value === null) return <>—</>;
  return <span className="num">{value}<span className="fb-of"> / 10</span></span>;
}

/**
 * Лента отзывов преподавателей об уроках. Один компонент на страницу «Отзывы» и на
 * вкладку карточки группы: при заданном groupId лента сужена до группы, а фильтр и
 * колонка «Группа» не нужны.
 */
export function LessonFeedbackTable({ groupId }: { groupId?: number }) {
  const search = useListSearchParams({ sortBy: 'lesson_date', sortDir: 'desc' });
  const { page, pageSize, filters, setPage, setPageSize, setFilters } = search;
  const debouncedFilters = useDeferredValue(filters);

  const { data: groups = [] } = useGroupsAll(true);
  const { data: teachers = [] } = useTeachers(true);

  // Перевёрнутый период сервер отклоняет 400, а keepPreviousData оставил бы на
  // экране старые строки без объяснения. Такой период не отправляем вовсе и
  // говорим об этом под полями дат (ISO-строки сравниваются лексикографически).
  const rangeInverted = Boolean(
    debouncedFilters.date_from && debouncedFilters.date_to
    && debouncedFilters.date_from > debouncedFilters.date_to,
  );
  const effectiveFilters = useMemo(() => {
    const base = { ...debouncedFilters };
    if (rangeInverted) { delete base.date_from; delete base.date_to; }
    return groupId ? { ...base, group_id: String(groupId) } : base;
  }, [debouncedFilters, groupId, rangeInverted]);

  const { data, isLoading, isFetching, isError, refetch } = useLessonFeedback({
    page, page_size: pageSize, filters: effectiveFilters,
  });
  const rows: LessonFeedbackRow[] = data?.rows ?? [];
  const total = data?.total ?? 0;

  const columns: Column<LessonFeedbackRow>[] = [
    {
      key: 'lesson_date', label: FEEDBACK_LABELS.colDate, sortable: false,
      cell: (r) => (
        <div>
          <span className="num">{fmtDate(r.lesson_date)}</span>
          {/* У отзыва «не состоялся» урока нет по определению — это не «удалён». */}
          {r.took_place === false
            ? <div className="fb-deleted">{FEEDBACK_LABELS.notHeld}</div>
            : r.lesson_id === null && <div className="fb-deleted">{FEEDBACK_LABELS.lessonDeleted}</div>}
        </div>
      ),
    },
    ...(groupId ? [] : [{
      key: 'group_id', label: FEEDBACK_LABELS.colGroup, sortable: false, searchable: true,
      searchOptions: groups.map((g) => ({ value: String(g.id), label: g.name })),
      cell: (r: LessonFeedbackRow) => <EntityLink section="groups" id={r.group_id} text={r.group_name} />,
    } satisfies Column<LessonFeedbackRow>]),
    {
      key: 'teacher_id', label: FEEDBACK_LABELS.colTeacher, sortable: false, searchable: true,
      searchOptions: teachers.map((t) => ({ value: String(t.id), label: t.name })),
      cell: (r) => r.teacher_id
        ? <EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name ?? `#${r.teacher_id}`} />
        : '—',
    },
    {
      key: 'understanding', label: FEEDBACK_LABELS.colUnderstanding, sortable: false,
      cell: (r) => <ScoreCell value={r.understanding} />,
    },
    {
      key: 'interest', label: FEEDBACK_LABELS.colInterest, sortable: false,
      cell: (r) => <ScoreCell value={r.interest} />,
    },
    {
      key: 'tech_problems', label: FEEDBACK_LABELS.colTech, sortable: false,
      // У несостоявшегося урока вопрос о тех. проблемах не задавался.
      cell: (r) => r.took_place === false ? '—' : r.tech_problems
        ? <span className="status-badge status-badge--warning">{FEEDBACK_LABELS.techYes}</span>
        : <span className="status-badge status-badge--muted">{FEEDBACK_LABELS.techNo}</span>,
    },
    {
      key: 'comment', label: FEEDBACK_LABELS.colComment, sortable: false,
      cell: (r) => <ClampText text={r.comment} className="clamp-text--column" />,
    },
    {
      key: 'record_url', label: FEEDBACK_LABELS.colRecord, sortable: false,
      cell: (r) => r.record_url
        ? <a href={r.record_url} target="_blank" rel="noopener noreferrer">{FEEDBACK_LABELS.openRecord}</a>
        : '—',
    },
    {
      key: 'submitted_at', label: FEEDBACK_LABELS.colSubmitted, sortable: false,
      cell: (r) => <span className="num">{fmtDateTime(r.submitted_at)}</span>,
    },
  ];

  if (isError && !data) {
    // Упавший запрос не должен выглядеть как «отзывов нет».
    return (
      <EmptyState
        hint={FEEDBACK_LABELS.errorHint}
        action={<button type="button" className="btn-cancel" onClick={() => { void refetch(); }}>{FEEDBACK_LABELS.retry}</button>}
      >
        {FEEDBACK_LABELS.errorTitle}
      </EmptyState>
    );
  }
  if (isLoading) return <TableSkeleton rows={8} cols={columns.length} />;

  // Даты живут в тех же f.*-фильтрах, что и селекты: «Сбросить» чистит всё разом.
  const setDate = (key: 'date_from' | 'date_to') => (e: { target: { value: string } }) => {
    const next = { ...filters };
    if (e.target.value) next[key] = e.target.value; else delete next[key];
    setFilters(next);
  };

  return (
    <DataTable<LessonFeedbackRow>
      data={rows}
      columns={columns}
      title={FEEDBACK_LABELS.title}
      isLoading={isFetching}
      headerActions={
        <div className="fb-dates">
          <DateInput value={filters.date_from ?? ''} onChange={setDate('date_from')} placeholder={FEEDBACK_LABELS.dateFrom} aria-label={FEEDBACK_LABELS.dateFrom} />
          <DateInput value={filters.date_to ?? ''} onChange={setDate('date_to')} placeholder={FEEDBACK_LABELS.dateTo} aria-label={FEEDBACK_LABELS.dateTo} />
          {rangeInverted && <div className="field-hint" role="alert">{FEEDBACK_LABELS.rangeInverted}</div>}
        </div>
      }
      serverPagination={{
        page, pageSize, total,
        sortBy: 'lesson_date', sortDir: 'desc', filters,
        onPageChange: setPage,
        onPageSizeChange: setPageSize,
        onSortChange: () => {},
        onFiltersChange: setFilters,
      }}
    />
  );
}
