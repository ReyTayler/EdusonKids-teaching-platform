import { useMemo, useDeferredValue } from 'react';
import { usePayroll, usePayrollSummary } from '../../hooks/usePayroll';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import type { ListSearchState, ListSearchControls } from '../../hooks/useListSearchParams';
import { DataTable, type Column } from '../../components/table/DataTable';
import { DateInput } from '../../components/form/DateInput';
import { EntityLink } from '../../components/EntityLink';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { fmtDate } from '../../lib/format';
import type { PayrollEntry } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';

export default function PayrollPage() {
  const search = useListSearchParams({ sortBy: 'lesson_date', sortDir: 'desc' });
  const mode = (search.getExtra('mode') === 'summary') ? 'summary' : 'list';
  const setMode = (m: 'list' | 'summary') => search.setExtra('mode', m === 'list' ? null : 'summary');

  return (
    <>
      {/* Шапка живёт во внешнем компоненте, а загрузку показывают вложенные
          представления — поэтому заголовок не пропадает при переключении вида. */}
      <PageHeader
        title="Зарплата"
        actions={
          <div className="segmented" role="group" aria-label="Режим отображения">
            <button
              type="button"
              className={`segmented__btn${mode === 'list' ? ' is-active' : ''}`}
              aria-pressed={mode === 'list'}
              onClick={() => setMode('list')}
            >Список</button>
            <button
              type="button"
              className={`segmented__btn${mode === 'summary' ? ' is-active' : ''}`}
              aria-pressed={mode === 'summary'}
              onClick={() => setMode('summary')}
            >Сводка</button>
          </div>
        }
      />

      {mode === 'list' ? (
        <PayrollListView search={search} />
      ) : (
        <PayrollSummaryView search={search} />
      )}
    </>
  );
}

type SearchProps = ListSearchState & ListSearchControls;

function PayrollListView({ search }: { search: SearchProps }) {
  const { page, pageSize, sortBy, sortDir, filters, setPage, setPageSize, setSort, setFilters, getExtra, setExtra, setExtras } = search;

  const globalDateRange = useMemo(() => ({
    from: getExtra('date_from') || '',
    to: getExtra('date_to') || '',
  }), [getExtra]);

  const effectiveFilters = useMemo(() => {
    const f = { ...filters };
    if (globalDateRange.from) f.date_from = globalDateRange.from;
    if (globalDateRange.to)   f.date_to   = globalDateRange.to;
    return f;
  }, [filters, globalDateRange]);

  const deferredFilters = useDeferredValue(effectiveFilters);

  const { data, isLoading, isFetching } = usePayroll({
    page,
    page_size: pageSize,
    sort_by: sortBy,
    sort_dir: sortDir,
    filters: deferredFilters,
  });

  const rows: PayrollEntry[] = data?.rows || [];
  const total = data?.total || 0;

  const columns: Column<PayrollEntry>[] = [
    { key: 'lesson_date', label: 'Дата', sortable: true, searchable: false,
      cell: (r) => <EntityLink section="lessons" id={r.lesson_id} text={fmtDate(r.lesson_date)} /> },
    { key: 'teacher_name', label: 'Преподаватель', sortable: true, searchable: true,
      cell: (r) => <EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name} /> },
    { key: 'group_name', label: 'Группа', sortable: true, searchable: true,
      cell: (r) => <EntityLink section="lessons" id={r.lesson_id} text={r.group_name} /> },
    { key: 'lesson_number', label: 'Урок #', sortable: true,
      cell: (r) => r.lesson_number },
    { key: 'present_count', label: 'Было/Всего', sortable: false,
      cell: (r) => `${r.present_count}/${r.total_students}` },
    { key: 'payment', label: 'Оплата ₽', sortable: true,
      cell: (r) => Number(r.payment).toLocaleString('ru') },
    { key: 'penalty', label: 'Штраф ₽', sortable: true,
      cell: (r) => Number(r.penalty).toLocaleString('ru') },
  ];

  return (
    <>
      <div className="payroll-range">
        <label>Период:</label>
        <DateInput
          value={globalDateRange.from}
          onChange={(e) => setExtra('date_from', e.target.value || null)}
          placeholder="от"
        />
        <span className="payroll-range__sep">—</span>
        <DateInput
          value={globalDateRange.to}
          onChange={(e) => setExtra('date_to', e.target.value || null)}
          placeholder="до"
        />
        <button
          className="btn-secondary"
          onClick={() => setExtras({ date_from: null, date_to: null })}
        >Сбросить</button>
      </div>
      <DataTable<PayrollEntry>
        data={rows}
        columns={columns}
        title="Список выплат"
        isLoading={isLoading || isFetching}
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
    </>
  );
}

function PayrollSummaryView({ search }: { search: SearchProps }) {
  const { getExtra, setExtra, setExtras } = search;
  const dateFrom = getExtra('date_from') || '';
  const dateTo = getExtra('date_to') || '';

  const { data, isLoading } = usePayrollSummary({
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  });
  if (isLoading) return <TableSkeleton rows={5} cols={4} />;
  const rows = data || [];

  const totalPayment = rows.reduce((acc, r) => acc + Number(r.sum_payment || 0), 0);
  const totalPenalty = rows.reduce((acc, r) => acc + Number(r.sum_penalty || 0), 0);
  const totalLessons = rows.reduce((acc, r) => acc + Number(r.lessons_count || 0), 0);

  return (
    <>
      <div className="payroll-range">
        <label>Период:</label>
        <DateInput value={dateFrom} onChange={(e) => setExtra('date_from', e.target.value || null)} placeholder="от" />
        <span className="payroll-range__sep">—</span>
        <DateInput value={dateTo} onChange={(e) => setExtra('date_to', e.target.value || null)} placeholder="до" />
        <button className="btn-secondary" onClick={() => setExtras({ date_from: null, date_to: null })}>Сбросить</button>
      </div>
      <div className="data-table__scroll">
        <table className="data-table">
          <thead>
            <tr><th>Преподаватель</th><th>Уроков</th><th>Сумма оплат ₽</th><th>Сумма штрафов ₽</th></tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={4} style={{ textAlign: 'center', padding: 24, color: 'var(--text3)' }}>
                Нет данных за выбранный период
              </td></tr>
            ) : (
              <>
                {rows.map((r) => (
                  <tr key={r.teacher_id}>
                    <td><EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name} /></td>
                    <td>{r.lessons_count}</td>
                    <td>{Number(r.sum_payment).toLocaleString('ru')}</td>
                    <td>{Number(r.sum_penalty).toLocaleString('ru')}</td>
                  </tr>
                ))}
                <tr style={{ background: 'var(--bg3)', fontWeight: 600 }}>
                  <td>Итого</td>
                  <td>{totalLessons}</td>
                  <td>{totalPayment.toLocaleString('ru')}</td>
                  <td>{totalPenalty.toLocaleString('ru')}</td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
