import { useNavigate } from 'react-router-dom';
import { DataTable, type Column, type ServerPaginationState, type ServerPaginationCallbacks } from '../../../components/table/DataTable';
import { EmptyState } from '../../../components/ui/EmptyState';
import { fmtDate, fmtLessons } from '../../../lib/format';
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
  /** Упавший запрос — не «пусто»: иначе ошибка читается как достоверный ноль. */
  isError?: boolean;
  /** Заголовок таблицы: меняется в режиме «Без группы». */
  title?: string;
}

// Остаток уроков курса. null — нет активного курса; отрицательный — проведено
// больше, чем в курсе (не долг и не ошибка, поэтому без красного).
function RemainingCell({ value }: { value: number | null }) {
  if (value === null) return <span className="reg-balance reg-balance--zero">—</span>;
  if (value < 0) {
    return (
      <span className="reg-balance">
        {fmtLessons(value)}{' '}
        <span className="reg-badge reg-badge--over" title="Проведено больше уроков, чем в курсе">
          сверх курса
        </span>
      </span>
    );
  }
  return (
    <span className={`reg-balance${value === 0 ? ' reg-balance--zero' : ''}`}>{fmtLessons(value)}</span>
  );
}

export function RegistryTable({ rows, serverPagination, isLoading, isError = false, title = 'Ученики' }: Props) {
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
      label: 'Осталось уроков',
      sortable: true,
      sortKey: 'balance',
      cell: (r) => <RemainingCell value={r.balance} />,
    },
    {
      key: 'cancellations',
      label: 'Отмены',
      sortable: false,
      // Показываем только если есть: колонка из нулей — шум.
      cell: (r) =>
        r.cancellations > 0 ? (
          <span
            className="reg-badge reg-badge--cancel"
            title="Отмены занятий в этом месяце в группах ученика"
          >
            {r.cancellations}
          </span>
        ) : null,
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

  if (isError) {
    return (
      <EmptyState hint="Обновите страницу или попробуйте чуть позже. Данные не потеряны.">
        Не удалось загрузить список учеников
      </EmptyState>
    );
  }

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
