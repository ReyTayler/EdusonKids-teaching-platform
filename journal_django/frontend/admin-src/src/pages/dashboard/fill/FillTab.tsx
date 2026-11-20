import { useListSearchParams } from '../../../hooks/useListSearchParams';
import { useUnfilledLessons } from '../../../hooks/useUnfilledLessons';
import { FillTable } from './FillTable';

/**
 * Раздел «Заполнить» дашборда — школьный список просроченных незаполненных
 * уроков (план + доп.уроки/отработки) с опциональным фильтром по преподавателю.
 * Фильтр и пагинация — в URL (see useListSearchParams), teacher — extra-параметр.
 * Лупа (FillSearch) живёт в шапке дашборда.
 */
export default function FillTab() {
  const s = useListSearchParams({ sortBy: 'date', sortDir: 'desc', pageSize: 30 });
  const {
    page, pageSize, sortBy, sortDir, filters,
    setPage, setPageSize, setSort, setFilters, getExtra,
  } = s;

  const rawTeacher = getExtra('teacher');
  const teacherId = rawTeacher && /^\d+$/.test(rawTeacher) ? Number(rawTeacher) : null;

  const q = useUnfilledLessons({ page, page_size: pageSize, teacher_id: teacherId, sort_dir: sortDir });
  const total = q.data?.total ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {total === 0 && !q.isFetching ? (
        <div className="cal-empty">Все уроки заполнены 🎉</div>
      ) : (
        <FillTable
          rows={q.data?.rows || []}
          isLoading={q.isFetching}
          serverPagination={{
            page,
            pageSize,
            total,
            sortBy,
            sortDir,
            filters,
            onPageChange: setPage,
            onPageSizeChange: setPageSize,
            onSortChange: setSort,
            onFiltersChange: setFilters,
          }}
        />
      )}
    </div>
  );
}
