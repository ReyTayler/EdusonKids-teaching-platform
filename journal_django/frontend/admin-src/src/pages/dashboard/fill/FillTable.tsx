import { useNavigate } from 'react-router-dom';
import { DataTable, type Column, type ServerPaginationState, type ServerPaginationCallbacks } from '../../../components/table/DataTable';
import { fmtDate } from '../../../lib/format';
import type { UnfilledLesson } from '../../../lib/shared-types';

interface Props {
  rows: UnfilledLesson[];
  serverPagination: ServerPaginationState & ServerPaginationCallbacks;
  isLoading: boolean;
}

export function FillTable({ rows, serverPagination, isLoading }: Props) {
  const navigate = useNavigate();

  const columns: Column<UnfilledLesson>[] = [
    {
      key: 'date',
      label: 'Дата',
      sortable: true,
      sortKey: 'date',
      cell: (r) => (
        <span>
          {fmtDate(r.date)}{r.time ? `, ${r.time}` : ''}
        </span>
      ),
    },
    {
      key: 'group_name',
      label: 'Группа',
      sortable: false,
      cell: (r) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)' }}>
          <span aria-hidden className="fin-dir-dot" style={{ background: r.direction_color || 'var(--text3)' }} />
          {r.group_name}
        </span>
      ),
    },
    {
      key: 'teacher_name',
      label: 'Преподаватель',
      sortable: false,
      cell: (r) => r.teacher_name || '—',
    },
    {
      key: 'direction_name',
      label: 'Направление',
      sortable: false,
      cell: (r) => r.direction_name || '—',
    },
    {
      key: 'lesson_number',
      label: '№',
      sortable: false,
      cell: (r) => (r.lesson_number != null ? String(r.lesson_number) : '—'),
    },
    {
      key: 'kind',
      label: 'Тип',
      sortable: false,
      cell: (r) =>
        r.kind === 'extra'
          ? <span className="nav-badge" title="Доп.урок (отработка)">Доп.</span>
          : <span style={{ color: 'var(--text3)' }}>Урок</span>,
    },
  ];

  const goFill = (r: UnfilledLesson) => {
    if (r.kind === 'extra') navigate('/admin/extra-lessons');
    else navigate(`/admin/groups/${r.group_id}?tab=lessons`);
  };

  return (
    <DataTable<UnfilledLesson>
      data={rows}
      columns={columns}
      title="Незаполненные уроки"
      onRowClick={goFill}
      serverPagination={serverPagination}
      isLoading={isLoading}
    />
  );
}
