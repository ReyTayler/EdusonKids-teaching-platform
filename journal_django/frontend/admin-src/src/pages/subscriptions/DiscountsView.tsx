import { useState } from 'react';
import { useDiscounts, useDiscountMutations } from '../../hooks/useDiscounts';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { Checkbox } from '../../components/form/Checkbox';
import { useAuth } from '../../hooks/useAuth';
import { canWriteSubscriptions, type Role } from '../../lib/permissions';

const FILL = 'Заполните поле';

function fmtPct(amount: number | string): string {
  const n = Number(amount);
  if (!Number.isFinite(n)) return '—';
  const pct = n * 100;
  if (Number.isInteger(pct)) return `${pct}%`;
  return `${pct.toFixed(1).replace('.', ',')}%`;
}

export function DiscountsView() {
  const discounts = useDiscounts(true);
  const muts = useDiscountMutations();
  const { toast } = useToast();
  const showError = useApiError();

  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState('');
  const [pct, setPct] = useState('');
  const [errors, setErrors] = useState<{ name?: string; pct?: string }>({});

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editPct, setEditPct] = useState('');
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const { me } = useAuth();
  const canWrite = canWriteSubscriptions(me?.role as Role);

  if (discounts.isLoading) return <TableSkeleton rows={5} cols={4} />;

  const rows = (discounts.data || []).slice();

  const validate = () => {
    const e: { name?: string; pct?: string } = {};
    if (!name.trim()) e.name = FILL;
    const num = Number(pct.replace(',', '.'));
    if (!pct.trim() || !Number.isFinite(num)) e.pct = FILL;
    else if (num < 0 || num > 100) e.pct = 'Должно быть от 0 до 100';
    return e;
  };

  const handleAdd = async () => {
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length > 0) return;
    const amount = Number(pct.replace(',', '.')) / 100;
    try {
      await muts.create.mutateAsync({ name: name.trim(), amount });
      toast('Скидка добавлена', 'ok');
      setName(''); setPct(''); setShowAdd(false); setErrors({});
    } catch (err) { showError(err); }
  };

  const startEdit = (row: { id: number; name: string; amount: number | string }) => {
    setEditingId(row.id);
    setEditName(row.name);
    setEditPct(String(Number(row.amount) * 100));
  };

  const saveEdit = async (id: number) => {
    const trimmed = editName.trim();
    const num = Number(editPct.replace(',', '.'));
    if (!trimmed) { toast('Введите название', 'error'); return; }
    if (!Number.isFinite(num) || num < 0 || num > 100) {
      toast('Скидка должна быть от 0 до 100', 'error'); return;
    }
    try {
      await muts.update.mutateAsync({ id, body: { name: trimmed, amount: num / 100 } });
      toast('Сохранено', 'ok');
      setEditingId(null);
    } catch (err) { showError(err); }
  };

  const toggleActive = async (id: number, active: boolean) => {
    try {
      await muts.update.mutateAsync({ id, body: { active: !active } });
    } catch (err) { showError(err); }
  };

  const handleDelete = async (id: number) => {
    if (confirmingId !== id) { setConfirmingId(id); return; }
    try {
      await muts.remove.mutateAsync(id);
      toast('Скидка удалена', 'ok');
      setConfirmingId(null);
    } catch (err) { showError(err); setConfirmingId(null); }
  };

  return (
    <>
      {canWrite && !showAdd && (
        <div className="view-actions">
          <button type="button" className="btn-add" onClick={() => setShowAdd(true)}>
            + Новая скидка
          </button>
        </div>
      )}

      {showAdd && (
        <div className="discount-add">
          <div className="discount-add__field">
            <input
              type="text"
              placeholder="Название скидки"
              value={name}
              onChange={(e) => { setName(e.target.value); setErrors(p => ({ ...p, name: undefined })); }}
            />
            {errors.name && <div className="field-error">{errors.name}</div>}
          </div>
          <div className="discount-add__field">
            <input
              type="number"
              placeholder="Размер скидки (%)"
              min={0}
              max={100}
              step="0.1"
              value={pct}
              onChange={(e) => { setPct(e.target.value); setErrors(p => ({ ...p, pct: undefined })); }}
            />
            {errors.pct && <div className="field-error">{errors.pct}</div>}
          </div>
          <button type="button" className="btn-save" onClick={() => void handleAdd()}>Добавить</button>
          <button type="button" className="btn-cancel" onClick={() => { setShowAdd(false); setName(''); setPct(''); setErrors({}); }}>Отмена</button>
        </div>
      )}

      <div className="table-panel">
        <div className="table-wrap">
        <table className="data-table">
        <thead>
          <tr>
            <th>Название</th>
            <th>Размер</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={4} style={{ textAlign: 'center', padding: 24, color: 'var(--text3)' }}>
              Нет скидок — нажми «+ Новая скидка», чтобы создать первую.
            </td></tr>
          ) : rows.map((d) => {
            const isEditing = editingId === d.id;
            return (
              <tr key={d.id} style={{ opacity: d.active ? 1 : 0.5 }}>
                <td>
                  {isEditing ? (
                    <input type="text" value={editName} onChange={(e) => setEditName(e.target.value)} style={{ width: '100%' }} />
                  ) : d.name}
                </td>
                <td>
                  {isEditing ? (
                    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                      <input type="number" min={0} max={100} step="0.1" value={editPct} onChange={(e) => setEditPct(e.target.value)} style={{ width: 80 }} />
                      <span>%</span>
                    </span>
                  ) : fmtPct(d.amount)}
                </td>
                <td>
                  <Checkbox
                    checked={d.active}
                    onChange={() => void toggleActive(d.id, d.active)}
                    label={d.active ? 'Активна' : 'Неактивна'}
                  />
                </td>
                <td style={{ textAlign: 'right' }}>
                  {isEditing ? (
                    <>
                      <button type="button" className="btn-link" onClick={() => void saveEdit(d.id)}>Сохранить</button>
                      {' · '}
                      <button type="button" className="btn-link" onClick={() => setEditingId(null)}>Отмена</button>
                    </>
                  ) : (
                    canWrite && (
                      <>
                        <button type="button" className="btn-link" onClick={() => startEdit(d)}>Изменить</button>
                        {' · '}
                        <button
                          type="button"
                          className={`btn-link${confirmingId === d.id ? ' is-confirming' : ''}`}
                          style={{ color: 'var(--bad)' }}
                          onClick={() => void handleDelete(d.id)}
                        >{confirmingId === d.id ? 'Точно удалить?' : 'Удалить'}</button>
                      </>
                    )
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
        </table>
        </div>
      </div>
    </>
  );
}
