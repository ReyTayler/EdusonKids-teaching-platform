import { useNavigate } from 'react-router-dom';
import { useArchive } from '../../hooks/useArchive';
import { useQueryClient } from '@tanstack/react-query';
import { useToast } from '../../components/ui/Toast';
import { TableSkeleton } from '../../components/ui/Skeleton';
import type { Teacher, Group, Direction } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';

export default function ArchivePage() {
  const { data, isLoading } = useArchive();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { toast } = useToast();

  // Шапка рисуется и во время загрузки — иначе заголовок пропадает
  // при каждом переходе в раздел.
  const header = (
    <PageHeader
      title="Архив"
      sub="Архивные сущности сохраняют историю и не участвуют в подборах."
      actions={
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            qc.invalidateQueries({ queryKey: ['archive'] });
            toast('Архив обновлён', 'ok');
          }}
        >↻ Обновить</button>
      }
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={3} cols={4} /></>;
  if (!data) return null;

  return (
    <>
      {header}

      <ArchiveSection
        label="Преподаватели"
        headers={['ID', 'Имя', 'Email']}
        rows={data.teachers}
        renderRow={(r: Teacher) => (
          <tr key={r.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/admin/teachers/${r.id}`)}>
            <td className="id-cell">#{r.id}</td>
            <td>{r.name}</td>
            <td style={{ color: 'var(--text3)' }}>{r.email || '—'}</td>
          </tr>
        )}
      />
      <ArchiveSection
        label="Группы"
        headers={['ID', 'Название']}
        rows={data.groups}
        renderRow={(r: Group) => (
          <tr key={r.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/admin/groups/${r.id}`)}>
            <td className="id-cell">#{r.id}</td>
            <td>{r.name}</td>
          </tr>
        )}
      />
      <ArchiveSection
        label="Направления"
        headers={['ID', 'Название']}
        rows={data.directions}
        renderRow={(r: Direction) => (
          <tr key={r.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/admin/directions/${r.id}`)}>
            <td className="id-cell">#{r.id}</td>
            <td>{r.name}</td>
          </tr>
        )}
      />
    </>
  );
}

interface SectionProps<T> {
  label: string;
  headers: string[];
  rows: T[];
  renderRow: (r: T) => React.ReactNode;
}

function ArchiveSection<T>({ label, headers, rows, renderRow }: SectionProps<T>) {
  return (
    <div className="archive-section">
      <div className="archive-section__head">
        {label}<span className="archive-section__count">{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <div className="archive-section__empty" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text3)', fontSize: 14, padding: 20 }}>
          Нет архивированных записей
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>{rows.map(renderRow)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
