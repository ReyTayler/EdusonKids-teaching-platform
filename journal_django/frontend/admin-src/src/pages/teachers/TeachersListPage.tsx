import { useState } from 'react';
import { useTeachers } from '../../hooks/useTeachers';
import { useGroupsAll } from '../../hooks/useGroups';
import { DataTable, type Column } from '../../components/table/DataTable';
import { RowOpenButton } from '../../components/table/RowOpenButton';
import { Avatar } from '../../components/Avatar';
import { Pill } from '../../components/ui/Pill';
import { TableSkeleton } from '../../components/ui/Skeleton';
import type { Teacher } from '../../lib/types';
import TeacherFormModal from './TeacherFormModal';
import { useAuth } from '../../hooks/useAuth';
import { canWriteTeachers, type Role } from '../../lib/permissions';
import { PageHeader } from '../../components/shell/PageHeader';

export default function TeachersListPage() {
  const { data, isLoading } = useTeachers();
  const { data: groups = [] } = useGroupsAll(true);
  const [modalOpen, setModalOpen] = useState(false);
  const { me } = useAuth();
  const canWrite = canWriteTeachers(me?.role as Role);

  const rows: Teacher[] = data || [];

  const columns: Column<Teacher>[] = [
    { key: 'id', label: 'ID', width: 72, cell: (r) => <span className="id-cell">{r.id}</span> },
    { key: 'name', label: 'Преподаватель', searchable: true,
      cell: (r) => (
        <div className="person-cell">
          <Avatar name={r.name} size={34} />
          <div><div className="person-name">{r.name}</div></div>
        </div>
      ) },
    { key: 'email', label: 'Email', searchable: true, cell: (r) => r.email || '—' },
    { key: 'phone', label: 'Телефон', searchable: true, cell: (r) => r.phone || '—' },
    { key: 'groups_count', label: 'Групп',
      cell: (r) => {
        const cnt = groups.filter((g) => g.teacher_id === r.id && g.active).length;
        return <Pill>{cnt}</Pill>;
      }},
    { key: 'active', label: 'Статус',
      cell: (r) => r.active
        ? <span className="badge badge--ok">Активен</span>
        : <span className="badge badge--muted">Архив</span> },
  ];

  // Шапка рисуется и во время загрузки: раньше страница возвращала
  // скелетон ДО неё, и заголовок пропадал при каждом переходе.
  const header = (
    <PageHeader
      title="Преподаватели"
      actions={canWrite ? <button className="btn-add" onClick={() => setModalOpen(true)}>+ Новый</button> : undefined}
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={6} cols={7} roomy /></>;

  return (
    <>
      {header}
      <DataTable<Teacher>
        data={rows}
        columns={columns}
        title="Преподаватели"
        roomy
        rowAction={(row) => <RowOpenButton to={`/admin/teachers/${row.id}`} />}
      />
      {modalOpen && (
        <TeacherFormModal initial={null} onClose={() => setModalOpen(false)} />
      )}
    </>
  );
}
