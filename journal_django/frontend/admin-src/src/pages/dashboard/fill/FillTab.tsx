import { useMemo } from 'react';
import { useListSearchParams } from '../../../hooks/useListSearchParams';
import { useTeachers } from '../../../hooks/useTeachers';
import { useUnfilledLessons } from '../../../hooks/useUnfilledLessons';
import { FillTable } from './FillTable';
import { FilterToolbar, TableFilterPanel, type FilterField } from '../../../components/filters';

/**
 * Раздел «Заполнить» дашборда — школьный список просроченных незаполненных
 * уроков (план + доп.уроки/отработки) с опциональным фильтром по преподавателю.
 * Фильтр и пагинация — в URL (see useListSearchParams), teacher — extra-параметр.
 */
export default function FillTab() {
  const s = useListSearchParams({ sortBy: 'date', sortDir: 'desc', pageSize: 30 });
  const {
    page, pageSize, sortBy, sortDir, filters,
    setPage, setPageSize, setSort, setFilters, getExtra, setExtra,
  } = s;

  const teachers = useTeachers(true); // включая архивных — у них тоже бывают старые долги
  const rawTeacher = getExtra('teacher');
  const teacherId = rawTeacher && /^\d+$/.test(rawTeacher) ? Number(rawTeacher) : null;

  const teacherOptions = useMemo(
    () => (teachers.data || []).slice().sort((a, b) => a.name.localeCompare(b.name))
      .map((t) => ({ value: String(t.id), label: t.name })),
    [teachers.data],
  );

  const searchFields: FilterField[] = [
    { kind: 'combobox', key: 'teacher', label: 'Преподаватель', options: teacherOptions, allLabel: 'Все преподаватели' },
  ];

  const q = useUnfilledLessons({ page, page_size: pageSize, teacher_id: teacherId, sort_dir: sortDir });
  const total = q.data?.total ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <FilterToolbar>
        <TableFilterPanel
          fields={searchFields}
          filters={teacherId != null ? { teacher: String(teacherId) } : {}}
          onApply={(next) => setExtra('teacher', next.teacher || null)}
          label="Поиск просроченных уроков"
        />
      </FilterToolbar>

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
