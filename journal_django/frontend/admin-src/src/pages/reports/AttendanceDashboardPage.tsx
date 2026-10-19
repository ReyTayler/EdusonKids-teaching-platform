import { useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { StatTiles } from '../../components/detail/StatTiles';
import { DataTable, type Column } from '../../components/table/DataTable';
import { RowOpenButton } from '../../components/table/RowOpenButton';
import { Dialog } from '../../components/ui/Dialog';
import { EmptyState } from '../../components/ui/EmptyState';
import { Field } from '../../components/form/Field';
import { DateInput } from '../../components/form/DateInput';
import StudentLessonsTable from '../../components/lessons/StudentLessonsTable';
import {
  useAttendanceDashboardStudents,
  useAttendanceDashboardSummary,
  type AttendanceSort,
} from '../../hooks/useAttendanceDashboard';
import { fmtDate, fmtLessons, todayMSK } from '../../lib/format';
import type { AttendanceDashboardRow } from '../../lib/types';

/** Первое и последнее число текущего месяца по МСК — период по умолчанию. */
function currentMonth(): { from: string; to: string } {
  const today = todayMSK();              // 'YYYY-MM-DD'
  const [y, m] = today.split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const mm = String(m).padStart(2, '0');
  return { from: `${y}-${mm}-01`, to: `${y}-${mm}-${String(last).padStart(2, '0')}` };
}

const lessons = (v: string | undefined) => fmtLessons(Number(v || 0));

/** Значение плитки: прочерк, пока данных нет. Ноль на денежном экране читается
 *  как достоверный факт «отработано ноль», а не как «ещё не загрузилось». */
const tile = (v: string | undefined) => (v == null ? '—' : lessons(v));

/**
 * Дашборд «Посещения учеников»: сколько уроков отработано всего и за период,
 * и как это раскладывается по ученикам. Клик по строке открывает детализацию
 * занятий этого ученика за тот же период.
 *
 * Счётчик везде один — списанные уроки с весом (45 минут = 0,5): обычные,
 * доп.уроки и сгорания. Бесплатные видны отдельной колонкой, но в «Итого» не
 * входят: с абонемента за них не списывается, и подмешать их значило бы
 * разойтись с балансами.
 */
export default function AttendanceDashboardPage() {
  // Ленивый инициализатор: период нужен только как стартовое значение стейта.
  const [month] = useState(currentMonth);
  const [dateFrom, setDateFrom] = useState(month.from);
  const [dateTo, setDateTo] = useState(month.to);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState<AttendanceSort>('billed');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [nameQuery, setNameQuery] = useState('');
  const [openStudent, setOpenStudent] = useState<AttendanceDashboardRow | null>(null);

  const summary = useAttendanceDashboardSummary({ dateFrom, dateTo });
  const students = useAttendanceDashboardStudents({
    dateFrom, dateTo, page, pageSize, sortBy, sortDir, nameQuery,
  });

  const columns: Column<AttendanceDashboardRow>[] = [
    { key: 'full_name', label: 'Ученик', searchable: true },
    { key: 'regular', label: 'Обычные', sortable: false, cell: (r) => lessons(r.regular) },
    { key: 'extra', label: 'Доп.уроки', sortable: false, cell: (r) => lessons(r.extra) },
    { key: 'burned', label: 'Сгоревшие', sortable: false, cell: (r) => lessons(r.burned) },
    { key: 'free', label: 'Бесплатные', sortable: false, cell: (r) => lessons(r.free) },
    { key: 'billed', label: 'Итого списано', cell: (r) => lessons(r.billed) },
  ];

  const periodLabel = `${fmtDate(dateFrom)} — ${fmtDate(dateTo)}`;

  return (
    <>
      <PageHeader
        title="Посещения учеников"
        sub="Отработанные уроки с учётом половинных занятий: 45 минут считаются за 0,5 урока."
      />

      <div className="page-toolbar">
        <Field label="Период с">
          <DateInput value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} />
        </Field>
        <Field label="по">
          <DateInput value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} />
        </Field>
      </div>

      <StatTiles
        items={[
          {
            label: 'Отработано за всё время',
            value: tile(summary.data?.all_time_lessons),
            sub: summary.isError ? 'не удалось загрузить' : 'по всем ученикам',
            subTone: summary.isError ? 'danger' : undefined,
          },
          {
            label: 'Отработано за период',
            value: tile(summary.data?.period_lessons),
            sub: summary.isError ? 'не удалось загрузить' : periodLabel,
            subTone: summary.isError ? 'danger' : undefined,
          },
        ]}
      />

      {students.isError ? (
        <EmptyState hint="Обновите страницу — если повторится, сообщите администратору.">
          Не удалось загрузить список учеников
        </EmptyState>
      ) : (
        <DataTable<AttendanceDashboardRow>
          data={students.data?.rows || []}
          columns={columns}
          title="Ученики за период"
          isLoading={students.isFetching}
          onRowClick={(row) => setOpenStudent(row)}
          rowAction={(row) => <RowOpenButton to={`/admin/students/${row.student_id}`} />}
          serverPagination={{
            page,
            pageSize,
            total: students.data?.total || 0,
            sortBy,
            sortDir,
            filters: nameQuery ? { full_name: nameQuery } : {},
            onPageChange: setPage,
            onPageSizeChange: (size) => { setPageSize(size); setPage(1); },
            onSortChange: (col, dir) => {
              setSortBy(col as AttendanceSort);
              setSortDir(dir);
              setPage(1);
            },
            onFiltersChange: (next) => { setNameQuery(next.full_name || ''); setPage(1); },
          }}
        />
      )}

      {openStudent && (
        <Dialog
          open
          onOpenChange={(o) => !o && setOpenStudent(null)}
          title={`${openStudent.full_name} · ${periodLabel}`}
          wide
        >
          <StudentLessonsTable
            studentId={openStudent.student_id}
            period={{ dateFrom, dateTo }}
            title={`Уроки ученика за период ${periodLabel}`}
          />
        </Dialog>
      )}
    </>
  );
}
