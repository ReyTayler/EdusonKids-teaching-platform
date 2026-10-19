import { useNavigate } from 'react-router-dom';
import { DataTable, type Column, type ServerPaginationState, type ServerPaginationCallbacks } from '../../../components/table/DataTable';
import { fmtDate } from '../../../lib/format';
import type { RegistryStudent } from '../../../lib/types';
import { RegistryStatusBadge } from './RegistryStatusBadge';

// «+N» для мультигрупп/мультипреподавателей: первый + счётчик остальных.
function firstPlus(items: string[]): string {
  if (items.length === 0) return '—';
  return items.length === 1 ? items[0] : `${items[0]} +${items.length - 1}`;
}

interface Props {
  rows: RegistryStudent[];
  serverPagination: ServerPaginationState & ServerPaginationCallbacks;
  isLoading: boolean;
  /** Заголовок таблицы: меняется в режиме «Без группы». */
  title?: string;
}

export function RegistryTable({ rows, serverPagination, isLoading, title = 'Ученики' }: Props) {
  const navigate = useNavigate();

  const columns: Column<RegistryStudent>[] = [
    {
      key: 'status',
      label: 'Статус',
      sortable: true,
      sortKey: 'urgency',
      cell: (r) => <RegistryStatusBadge status={r.status} />,
    },
    {
      key: 'codes',
      label: 'Код',
      sortable: false,
      cell: (r) => <span className="reg-cell-code">{firstPlus(r.codes)}</span>,
    },
    {
      key: 'student_name',
      label: 'Ученик',
      sortable: true,
      sortKey: 'name',
      searchable: true,
      cell: (r) => <span className="reg-cell-name">{r.student_name}</span>,
    },
    {
      key: 'teacher_names',
      label: 'Препод.',
      sortable: false,
      cell: (r) => firstPlus(r.teacher_names),
    },
    {
      key: 'progress',
      label: 'Прогресс',
      sortable: true,
      sortKey: 'progress',
      cell: (r) => (
        <div className="reg-progress">
          <span className="reg-progress__num">{r.attended}/{r.planned}</span>
          {r.progress_pct !== null && (
            <span className="reg-progress__bar">
              <span
                className="reg-progress__fill"
                style={{ width: `${Math.min(100, r.progress_pct)}%` }}
              />
            </span>
          )}
        </div>
      ),
    },
    {
      key: 'balance',
      label: 'Остаток',
      sortable: true,
      sortKey: 'balance',
      cell: (r) => (
        <span className={`reg-balance${r.balance <= 0 ? ' reg-balance--zero' : ''}`}>{r.balance}</span>
      ),
    },
    {
      key: 'last_lesson_date',
      label: 'Последний',
      sortable: true,
      sortKey: 'last_lesson',
      cell: (r) => fmtDate(r.last_lesson_date),
    },
    {
      key: 'next_lesson_date',
      label: 'Ближайший',
      sortable: false,
      cell: (r) => fmtDate(r.next_lesson_date),
    },
  ];

  return (
    <DataTable<RegistryStudent>
      data={rows}
      columns={columns}
      title={title}
      onRowClick={(r) => navigate(`/admin/students/${r.student_id}`)}
      isLoading={isLoading}
      serverPagination={serverPagination}
    />
  );
}
