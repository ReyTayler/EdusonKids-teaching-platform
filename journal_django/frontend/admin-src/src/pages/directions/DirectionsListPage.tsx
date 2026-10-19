import { useState } from 'react';
import { useDirections } from '../../hooks/useDirections';
import { useGroupsAll } from '../../hooks/useGroups';
import { DataTable, type Column } from '../../components/table/DataTable';
import { RowOpenButton } from '../../components/table/RowOpenButton';
import { Pill } from '../../components/ui/Pill';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { directionColor } from '../../lib/direction-color';
import { fmtRub } from '../../lib/format';
import type { Direction } from '../../lib/types';
import DirectionFormModal from './DirectionFormModal';
import { useAuth } from '../../hooks/useAuth';
import { canWriteDirections, type Role } from '../../lib/permissions';
import { PageHeader } from '../../components/shell/PageHeader';

/**
 * Раздел «Направления».
 *
 * Раньше был сеткой плиток с крупной цифрой «активных групп». Приведён к тому
 * же табличному виду, что и остальные списки сущностей: одинаковая шапка,
 * одинаковый шаг строки, тот же переход в карточку кнопкой «Открыть».
 *
 * Цвет направления сеткой держался заливкой всей плитки; в таблице его несёт
 * квадратная метка перед названием — сканирование раздела глазами по цвету
 * сохраняется, но не спорит с данными за внимание.
 *
 * Длина курса и цена абонемента живут вторым этажом ячейки названия, а не
 * отдельными колонками: они всегда читаются вместе с направлением, и на трёх
 * строках таблицы две почти пустые колонки выглядели бы разреженно.
 */
export default function DirectionsListPage() {
  const { data, isLoading } = useDirections();
  const { data: groups = [] } = useGroupsAll(true);
  const [modalOpen, setModalOpen] = useState(false);
  const { me } = useAuth();
  const canWrite = canWriteDirections(me?.role as Role);

  const rows = (data || []).filter((r) => r.active);

  const columns: Column<Direction>[] = [
    {
      key: 'id',
      label: 'ID',
      width: 72,
      cell: (r) => <span className="id-cell">{r.id}</span>,
    },
    {
      key: 'name',
      label: 'Направление',
      searchable: true,
      cell: (r) => {
        const spec = [
          r.total_lessons != null ? `${r.total_lessons} уроков в курсе` : null,
          r.subscription_price != null && r.subscription_price !== ''
            ? `абонемент ${fmtRub(r.subscription_price)}`
            : null,
        ].filter(Boolean).join(' · ');
        return (
          <div className="person-cell">
            <span className="dir-swatch" style={{ background: directionColor(r) }} aria-hidden="true" />
            <div className="cell-stack">
              <span className="cell-stack__main" style={{ fontWeight: 600 }}>{r.name}</span>
              {spec && <span className="cell-stack__sub">{spec}</span>}
            </div>
          </div>
        );
      },
    },
    {
      key: 'groups_count',
      label: 'Активных групп',
      cell: (r) => <Pill>{groups.filter((g) => g.direction_id === r.id && g.active).length}</Pill>,
    },
  ];

  // Шапка рисуется и во время загрузки — иначе заголовок пропадает
  // при каждом переходе в раздел.
  const header = (
    <PageHeader
      title="Направления"
      count={isLoading ? undefined : rows.length}
      actions={canWrite && (
        <button type="button" className="btn-add" onClick={() => setModalOpen(true)}>+ Новое</button>
      )}
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={4} cols={4} roomy /></>;

  return (
    <>
      {header}
      <DataTable<Direction>
        data={rows}
        columns={columns}
        title="Направления"
        roomy
        rowAction={(row) => <RowOpenButton to={`/admin/directions/${row.id}`} />}
      />
      {modalOpen && (
        <DirectionFormModal initial={null} onClose={() => setModalOpen(false)} />
      )}
    </>
  );
}
