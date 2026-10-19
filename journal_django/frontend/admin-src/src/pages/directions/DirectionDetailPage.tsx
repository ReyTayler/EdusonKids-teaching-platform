import { useState } from 'react';
import { useParams, Navigate, useNavigate } from 'react-router-dom';
import { useDirection, useDirectionMutations } from '../../hooks/useDirections';
import { useGroupsAll } from '../../hooks/useGroups';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { DetailShell, type DetailField } from '../../components/detail/DetailShell';
import { PageLoading } from '../../components/ui/Skeleton';
import { directionColor } from '../../lib/direction-color';
import type { Direction } from '../../lib/types';
import DirectionFormModal from './DirectionFormModal';
import { useAuth } from '../../hooks/useAuth';
import { canWriteDirections, type Role } from '../../lib/permissions';

export default function DirectionDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const navigate = useNavigate();
  const { data: direction, isLoading } = useDirection(id);
  const { data: groups = [] } = useGroupsAll();
  const muts = useDirectionMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [editing, setEditing] = useState(false);
  const { me } = useAuth();
  const canWrite = canWriteDirections(me?.role as Role);

  if (isLoading) return <PageLoading />;
  if (!direction) return <Navigate to="/admin/directions" replace />;

  const color = directionColor(direction);
  const myGroups = groups.filter((g) => g.direction_id === direction.id && g.active);

  const fields: DetailField<Direction>[] = [
    { key: 'id', label: 'ID' },
    { key: 'name', label: 'Название' },
    { key: 'total_lessons', label: 'Уроков на направление',
      cell: (r) => r.total_lessons == null ? '—' : String(r.total_lessons) },
    { key: 'color', label: 'Цвет',
      cell: (r) => r.color ? (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 14.5 }}>
          <span style={{ width: 18, height: 18, borderRadius: 5, background: r.color, border: '1px solid var(--border)' }} />
          {r.color}
        </span>
      ) : <span style={{ color: 'var(--text3)' }}>— не задан —</span> },
    { key: 'active', label: 'Статус', cell: (r) => r.active ? 'Активен' : 'Архив' },
  ];

  const handleDelete = async () => {
    try {
      await muts.remove.mutateAsync(direction.id);
      toast('Архивировано', 'ok');
      navigate('/admin/directions');
    } catch (err) { showError(err); }
  };

  const handleRestore = async () => {
    try {
      await muts.update.mutateAsync({ id: direction.id, body: { active: true } });
      toast('Разархивировано', 'ok');
    } catch (err) { showError(err); }
  };

  return (
    <>
      <DetailShell<Direction>
        title={direction.name}
        row={direction}
        fields={fields}
        cardTitle="Данные направления"
        onEdit={canWrite ? () => setEditing(true) : undefined}
        onDelete={canWrite ? handleDelete : undefined}
        archived={!direction.active}
        onRestore={canWrite ? handleRestore : undefined}
        backTo="/admin/directions"
        parentLabel="Направления"
      >
        <div className="sub-header">Группы <span className="count-badge">{myGroups.length}</span></div>
        {myGroups.length === 0 ? (
          <div className="memberships__empty">Нет активных групп</div>
        ) : myGroups.map((g) => (
          <div
            key={g.id}
            className="link-card"
            tabIndex={0}
            role="button"
            onClick={() => navigate(`/admin/groups/${g.id}`)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/admin/groups/${g.id}`); } }}
          >
            <div className="link-card-head">
              <div>
                <div className="link-card-title">{g.name}</div>
                <div className="link-card-meta">
                  <span style={{ fontSize: 13, color: 'var(--text3)' }}>
                    {g.lesson_duration_minutes} мин · {g.lessons_per_week}×/нед
                  </span>
                </div>
              </div>
              <span style={{ fontSize: 13, color: 'var(--text3)' }}>#{g.id}</span>
            </div>
          </div>
        ))}
        <div style={{ marginTop: 12, fontSize: 13, color: 'var(--text3)' }}>
          Цветовой акцент: <span style={{ color }}>{color}</span>
        </div>
      </DetailShell>
      {editing && (
        <DirectionFormModal initial={direction} onClose={() => setEditing(false)} />
      )}
    </>
  );
}
