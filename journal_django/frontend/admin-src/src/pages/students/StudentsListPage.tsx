import { useDeferredValue } from 'react';
import { useState } from 'react';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { useStudents } from '../../hooks/useStudents';
import { useRenewalAssignees } from '../../hooks/useRenewals';
import { useRenewalStages } from '../../hooks/useRenewalStages';
import { DataTable, type Column } from '../../components/table/DataTable';
import { RowOpenButton } from '../../components/table/RowOpenButton';
import { Avatar } from '../../components/Avatar';
import { StudentStageBadge } from '../../components/StudentStageBadge';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { fmtDate, fmtAge } from '../../lib/format';
import type { Student } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';
import StudentFormModal from './StudentFormModal';

export default function StudentsListPage() {
  const [modalOpen, setModalOpen] = useState(false);

  // URL-синхронизированный стейт пагинации и сортировки.
  const search = useListSearchParams({ sortBy: 'full_name', sortDir: 'asc' });
  const { page, pageSize, sortBy, sortDir, filters, setPage, setPageSize, setSort, setFilters } = search;
  const { data: assignees } = useRenewalAssignees();
  // Стадии воронки — варианты фильтра колонки «Стадия» (бэк ждёт filter[stage_id]).
  const { data: stages } = useRenewalStages();

  // Debounce фильтров — не гоним запрос на каждый символ.
  const debouncedFilters = useDeferredValue(filters);

  const { data, isLoading, isFetching } = useStudents({
    page,
    page_size: pageSize,
    sort_by: sortBy,
    sort_dir: sortDir,
    filters: debouncedFilters,
  });

  const rows: Student[] = data?.rows || [];
  const total = data?.total || 0;

  // Whitelist sortable/searchable строго по тому, что поддерживает бэкенд.
  // Client-side searchable: function удалена — бэк фильтрует сам.
  const columns: Column<Student>[] = [
    {
      key: 'id',
      label: 'ID',
      // Ширина задана явно: когда колонок мало, таблица тянется на 100% ширины
      // панели и делит излишек между колонками пропорционально содержимому —
      // без этого колонка из трёх цифр получала бы двести с лишним пикселей.
      width: 72,
      sortable: false,
      searchable: false,
      cell: (r) => <span className="id-cell">{r.id}</span>,
    },
    {
      key: 'full_name',
      label: 'Ученик',
      sortable: true,
      searchable: true,
      cell: (r) => (
        <div className="person-cell">
          <Avatar name={r.full_name} size={32} />
          <div>
            <div className="person-name">{r.full_name}</div>
            {/* Тёзки обычны: родитель и номер помогают отличить одного от другого. */}
            <div className="person-sub">{r.parent1_name ? `${r.parent1_name} · №${r.id}` : `№${r.id}`}</div>
          </div>
        </div>
      ),
    },
    {
      key: 'birth_date',
      label: 'Дата рожд.',
      sortable: false,
      searchable: false,
      cell: (r) => fmtDate(r.birth_date),
    },
    {
      // Возраст вычисляется из даты рождения (поле age удалено). Сортируем по
      // birth_date — он монотонно связан с возрастом; фильтра нет (вычисляемое
      // поле нельзя фильтровать на сервере без пересчёта в диапазон дат).
      key: 'age',
      label: 'Возраст',
      sortKey: 'birth_date',
      sortable: true,
      searchable: false,
      cell: (r) => fmtAge(r.birth_date),
    },
    {
      key: 'parent1_phone',
      label: 'Телефон родителя 1',
      sortable: false,
      searchable: true,
      cell: (r) => r.parent1_phone || '—',
    },
    {
      key: 'parent1_name',
      label: 'Родитель 1',
      sortable: false,
      searchable: true,
      cell: (r) => r.parent1_name || '—',
    },
    {
      key: 'platform_id',
      label: 'Platform ID',
      sortable: false,
      searchable: true,
      cell: (r) => r.platform_id || '—',
    },
    {
      key: 'manager_id',
      label: 'Менеджер',
      sortable: false,
      searchable: true,
      searchOptions: (assignees || []).map((a) => ({ value: String(a.id), label: a.full_name })),
      cell: (r) => r.manager_name || '—',
    },
    {
      // «Статус» ученика = стадия его последней сделки продления.
      // key = имя фильтра (бэк ждёт filter[stage_id]), sortKey — имя сортировки
      // (sort_by=stage, по sort_order стадии). Тот же приём, что у 'manager_id'.
      key: 'stage_id',
      label: 'Стадия',
      sortKey: 'stage',
      sortable: true,
      searchable: true,
      searchOptions: (stages || []).map((s) => ({ value: String(s.id), label: s.label })),
      cell: (r) => <StudentStageBadge row={r} />,
    },
  ];

  // Шапка рисуется и во время загрузки: раньше страница возвращала скелетон
  // ДО неё, и при каждом переходе между разделами заголовок пропадал и
  // появлялся заново — экран «моргал» названием.
  const header = (
    <PageHeader
      title="Ученики"
      count={isLoading ? undefined : total}
      actions={<button type="button" className="btn-add" onClick={() => setModalOpen(true)}>+ Новый</button>}
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={6} cols={10} roomy /></>;

  return (
    <>
      {header}
      <DataTable<Student>
        data={rows}
        columns={columns}
        title="Ученики"
        roomy
        rowAction={(row) => <RowOpenButton to={`/admin/students/${row.id}`} />}
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
        <StudentFormModal
          initial={null}
          onClose={() => setModalOpen(false)}
        />
      )}
    </>
  );
}
