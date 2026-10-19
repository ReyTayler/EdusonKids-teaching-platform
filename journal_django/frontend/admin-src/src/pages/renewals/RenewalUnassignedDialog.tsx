import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { EntityLink } from '../../components/EntityLink';
import { useRenewalMutations, useRenewalUnassigned } from '../../hooks/useRenewals';
import { useApiError } from '../../hooks/useApiError';
import { fmtLessons } from '../../lib/format';

interface Props {
  onClose: () => void;
}

/**
 * Сводка «Ученики без сделок»: новички — активные ученики, у которых сделки не
 * было ни разу. Менеджер создаёт сделку вручную, она сразу встаёт в актуальную
 * авто-стадию по посещаемости и балансу.
 *
 * Вернувшихся после «Ушёл» здесь НЕТ (решение 2026-07-27): их возвращают
 * переоткрытием закрытой сделки — так сохраняется номер цикла и прогресс.
 */
export function RenewalUnassignedDialog({ onClose }: Props) {
  const { data: rows, isLoading } = useRenewalUnassigned();
  const { create } = useRenewalMutations();
  const showError = useApiError();
  // id ученика, по которому мутация в полёте — дизейблим только его кнопку
  const [pendingId, setPendingId] = useState<number | null>(null);

  const handleCreate = (studentId: number) => {
    setPendingId(studentId);
    create.mutate({ student_id: studentId }, {
      onSettled: () => setPendingId(null),
      onError: (err) => showError(err, 'Не удалось создать сделку'),
    });
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => { if (!o) onClose(); }}
      title="Ученики без сделок"
      wide
      footer={<button type="button" className="btn-secondary" onClick={onClose}>Закрыть</button>}
    >
      <p className="renewal-close-dialog__text">
        Новички: занимаются, но сделки продления у них ещё не было ни разу.
        Создайте сделку — она сразу встанет в нужную стадию по посещаемости и
        балансу. Ученика, вернувшегося после ухода, здесь не будет: его сделку
        нужно переоткрыть в разделе «Список» → «Показать закрытые».
      </p>
      {isLoading ? (
        <div className="renewal-drawer__loading">Загружаем…</div>
      ) : (rows || []).length === 0 ? (
        <p className="renewal-close-dialog__text">
          Все активные ученики в воронке — сделки есть у каждого. 🎉
        </p>
      ) : (
        <div className="renewal-months__scroll">
          <table className="renewal-months renewal-unassigned">
            <thead>
              <tr>
                <th>Ученик</th>
                <th>Направления</th>
                <th>Посещено</th>
                <th>Цикл</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(rows || []).map((r) => (
                <tr key={r.student_id}>
                  <td className="renewal-unassigned__student">
                    <EntityLink section="students" id={r.student_id} text={r.student_name} />
                    {r.debt && (
                      <span className="status-badge status-badge--negative" title="Баланс ученика отрицательный">
                        Долг
                      </span>
                    )}
                  </td>
                  <td>
                    {(r.directions || []).map((d, i) => (
                      <span key={d.name} style={d.color ? { color: d.color } : undefined}>
                        {i > 0 && ', '}{d.name}
                      </span>
                    ))}
                    {(r.directions || []).length === 0 && '—'}
                  </td>
                  <td>{fmtLessons(r.attended)} ур.</td>
                  <td>Цикл {r.cycle_no}</td>
                  <td>
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={pendingId === r.student_id}
                      onClick={() => handleCreate(r.student_id)}
                    >
                      {pendingId === r.student_id ? 'Создаём…' : 'Создать сделку'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialog>
  );
}
