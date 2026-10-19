import { useState } from 'react';
import { useDirections, useDirectionMutations } from '../../hooks/useDirections';
import { usePayments } from '../../hooks/usePayments';
import { useApiError } from '../../hooks/useApiError';
import { NumberInput } from '../../components/form/NumberInput';
import { useToast } from '../../components/ui/Toast';
import { fmtRub } from '../../lib/format';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { useAuth } from '../../hooks/useAuth';
import { canWriteSubscriptions, type Role } from '../../lib/permissions';

export function SubscriptionsView() {
  const directions = useDirections();
  const payments = usePayments();
  const muts = useDirectionMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const { me } = useAuth();
  const canWrite = canWriteSubscriptions(me?.role as Role);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<string>('');

  if (directions.isLoading) return <TableSkeleton rows={5} cols={5} />;

  const rows = (directions.data || []).slice().sort((a, b) => a.name.localeCompare(b.name));

  const paymentsCountByDir = new Map<number, number>();
  for (const p of payments.data || []) {
    paymentsCountByDir.set(p.direction_id, (paymentsCountByDir.get(p.direction_id) || 0) + 1);
  }

  const startEdit = (id: number, current: number | null) => {
    setEditingId(id);
    setDraft(current != null ? String(current) : '');
  };
  const commit = async (id: number) => {
    const v = draft.trim();
    const num = v === '' ? null : Number(v);
    if (v !== '' && !Number.isFinite(num)) {
      toast('Введите число или оставьте пустым', 'error');
      return;
    }
    try {
      await muts.update.mutateAsync({ id, body: { subscription_price: num as number | null } });
      toast('Цена обновлена', 'ok');
      setEditingId(null);
    } catch (err) {
      showError(err);
    }
  };

  return (
    /* Панель + горизонтальная прокрутка — как у остальных таблиц системы.
       Раньше здесь была голая <table> прямо на холсте. */
    <div className="table-panel">
      <div className="table-wrap">
        <table className="data-table">
      <thead>
        <tr>
          <th>Направление</th>
          <th>Цена за абонемент</th>
          <th>Уроков в курсе</th>
          <th>Цена за урок</th>
          <th>Всего оплат</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => {
          const price = d.subscription_price != null ? Number(d.subscription_price) : null;
          const total = d.total_lessons || 0;
          const subs = total > 0 ? Math.floor(total / 4) : 0;
          const perLesson = price != null && price > 0 ? price / 4 : null;
          const count = paymentsCountByDir.get(d.id) || 0;
          const isEditing = editingId === d.id;
          return (
            <tr key={d.id}>
              <td>
                <span className="dir-swatch" style={{ display: 'inline-block', verticalAlign: 'middle', background: d.color || 'var(--t-unknown)' }} /> {d.name}
              </td>
              <td className="cell-num">
                {!canWrite ? (
                  <span>{price != null ? fmtRub(price) : <em>не настроено</em>}</span>
                ) : isEditing ? (
                  <span className="inline-edit">
                    <NumberInput
                      min={0}
                      step="0.01"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') { void commit(d.id); }
                        if (e.key === 'Escape') { setEditingId(null); }
                      }}
                      autoFocus
                      style={{ width: 120 }}
                    />
                    <button type="button" className="btn-link" onClick={() => { void commit(d.id); }}>Сохранить</button>
                    <button type="button" className="btn-link" onClick={() => setEditingId(null)}>Отмена</button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn-link"
                    onClick={() => startEdit(d.id, price)}
                    title="Изменить цену"
                  >
                    {price != null ? fmtRub(price) : <em>не настроено</em>}
                    {' '}
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                      <path d="M11.013 1.427a1.75 1.75 0 0 1 2.474 0l1.086 1.086a1.75 1.75 0 0 1 0 2.474l-8.61 8.61c-.21.21-.47.364-.756.445l-3.251.93a.75.75 0 0 1-.927-.928l.929-3.25c.081-.286.235-.547.445-.758l8.61-8.61Zm1.414 1.06a.25.25 0 0 0-.354 0L10.811 3.75l1.439 1.44 1.263-1.263a.25.25 0 0 0 0-.354Zm.262 2.617-1.44-1.44-6.625 6.626-.353 1.237 1.237-.353Z" />
                    </svg>
                  </button>
                )}
              </td>
              <td>
                {total > 0 ? `${total} (${subs} абон.)` : <em>не задан курс</em>}
              </td>
              <td className="cell-num">{perLesson != null ? fmtRub(perLesson) : '—'}</td>
              <td className="cell-num">{count}</td>
            </tr>
          );
        })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
