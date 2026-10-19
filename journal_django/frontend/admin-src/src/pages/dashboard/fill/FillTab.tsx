import { useMemo } from 'react';
import { useListSearchParams } from '../../../hooks/useListSearchParams';
import { useTeachers } from '../../../hooks/useTeachers';
import { useUnfilledLessons } from '../../../hooks/useUnfilledLessons';
import { FillTable } from './FillTable';
import { Field } from '../../../components/form/Field';
import { Combobox } from '../../../components/form/Combobox';

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

  const q = useUnfilledLessons({ page, page_size: pageSize, teacher_id: teacherId, sort_dir: sortDir });
  const total = q.data?.total ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{ maxWidth: 320 }}>
        <Field label="Преподаватель">
          <Combobox
            value={teacherId != null ? String(teacherId) : ''}
            onChange={(v) => setExtra('teacher', v || null)}
            options={teacherOptions}
            placeholder="Все преподаватели"
          />
        </Field>
      </div>

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
