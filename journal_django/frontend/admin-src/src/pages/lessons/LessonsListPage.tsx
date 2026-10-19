// web/admin/src/pages/lessons/LessonsListPage.tsx
import { useDeferredValue } from 'react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { useLessons } from '../../hooks/useLessons';
import { useAuth } from '../../hooks/useAuth';
import { canWriteLessons, type Role } from '../../lib/permissions';
import { DataTable, type Column } from '../../components/table/DataTable';
import { EntityLink } from '../../components/EntityLink';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { fmtDate } from '../../lib/format';
import { LESSON_TYPE_LABELS, LESSON_TYPE_OPTIONS } from '../../lib/labels';
import type { Lesson } from '../../lib/types';
import LessonFormModal from './LessonFormModal';
import { PageHeader } from '../../components/shell/PageHeader';

export default function LessonsListPage() {
  const navigate = useNavigate();
  const [modalOpen, setModalOpen] = useState(false);
  const { me } = useAuth();
  const canWrite = canWriteLessons(me?.role as Role);

  // URL-синхронизированный стейт пагинации и сортировки.
  const search = useListSearchParams({ sortBy: 'lesson_date', sortDir: 'desc' });
  const { page, pageSize, sortBy, sortDir, filters, setPage, setPageSize, setSort, setFilters } = search;

  // Debounce фильтров: не делаем запрос на каждый нажатый символ.
  // Sort и page не дебаунсим — они меняются по клику.
  const debouncedFilters = useDeferredValue(filters);

  const { data, isLoading, isFetching } = useLessons({
    page,
    page_size: pageSize,
    sort_by: sortBy,
    sort_dir: sortDir,
    filters: debouncedFilters,
  });

  const rows: Lesson[] = data?.rows || [];
  const total = data?.total || 0;

  // Whitelist sortable/searchable по бэк-supported:
  // lesson_date, lesson_number, group_name, teacher_name, lesson_type
  // Остальные (id, record_url, lesson_duration_minutes) — sortable:false, searchable:false
  const columns: Column<Lesson>[] = [
    {
      key: 'id',
      label: 'ID',
      sortable: false,
      searchable: false,
      cell: (r) => <span className="id-cell">#{r.id}</span>,
    },
    {
      key: 'lesson_date',
      label: 'Дата',
      sortable: true,
      searchable: false,
      cell: (r) => fmtDate(r.lesson_date),
    },
    {
      key: 'group_name',
      label: 'Группа',
      sortable: true,
      searchable: true,
      cell: (r) => <EntityLink section="groups" id={r.group_id} text={r.group_name} />,
    },
    {
      key: 'teacher_name',
      label: 'Преподаватель',
      sortable: true,
      searchable: true,
      cell: (r) => <EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name} />,
    },
    {
      key: 'lesson_number',
      label: 'Урок #',
      sortable: true,
      searchable: false,
    },
    {
      key: 'lesson_type',
      label: 'Тип',
      sortable: true,
      searchable: true,
      searchOptions: LESSON_TYPE_OPTIONS,
      cell: (r) => LESSON_TYPE_LABELS[r.lesson_type] || r.lesson_type,
    },
  ];


  // Шапка рисуется и во время загрузки: раньше страница возвращала
  // скелетон ДО неё, и заголовок пропадал при каждом переходе.
  const header = (
    <PageHeader
      title="Уроки"
      count={isLoading ? undefined : total}
      actions={canWrite && <button className="btn-add" onClick={() => setModalOpen(true)}>+ Новый</button>}
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={8} cols={columns.length} /></>;

  return (
    <>
      {header}
      <DataTable<Lesson>
        data={rows}
        columns={columns}
        title="Уроки"
        onRowClick={(row) => navigate(`/admin/lessons/${row.id}`)}
        isLoading={isFetching}
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
      {modalOpen && (
        <LessonFormModal onClose={() => setModalOpen(false)} />
      )}
    </>
  );
}
