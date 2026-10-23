import { useState } from 'react';
import { useCancellationReasons, useCancellationReasonMutations } from '../../hooks/useCancellationReasons';
import { useApiError } from '../../hooks/useApiError';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { ActionMenu } from '../../components/ui/ActionMenu';
import { EmptyState } from '../../components/ui/EmptyState';
import { BlockLoading } from '../../components/ui/Skeleton';
import { Switch } from '../../components/form/Switch';
import { CANCELLATION_LABELS, CANCELLATION_SIDE_LABELS } from '../../lib/labels';
import type { CancellationReason, CancellationSide } from '../../lib/types';
import { CancellationReasonDialog } from './CancellationReasonDialog';

const SIDES: CancellationSide[] = ['teacher', 'client'];

type DialogState = { side: CancellationSide; editing: CancellationReason | null } | null;

/**
 * «Настройки → Отмены уроков» (только superadmin): справочник причин по сторонам.
 * Флаг «Компенсация» решает, платят ли преподавателю за отменённый урок, поэтому
 * экран закрыт для остальных ролей (правило мотивации) — маршрут уже за RequireRole.
 */
export function CancellationReasonsTab() {
  const q = useCancellationReasons();
  const { update, remove } = useCancellationReasonMutations();
  const apiError = useApiError();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [deleting, setDeleting] = useState<CancellationReason | null>(null);
  // Строки, чей PATCH сейчас идёт: переключатель такой строки недоступен до ответа,
  // а две быстрые правки разных строк не затирают друг друга.
  const [toggling, setToggling] = useState<Set<number>>(() => new Set());

  if (q.isLoading) return <BlockLoading rows={4} />;
  // Упавший запрос не должен выглядеть как «причин нет»: это про деньги преподавателей.
  if (q.isError || !q.data) {
    return (
      <EmptyState
        hint={CANCELLATION_LABELS.settingsErrorHint}
        action={<button type="button" className="btn-cancel" onClick={() => q.refetch()}>{CANCELLATION_LABELS.retry}</button>}
      >
        {CANCELLATION_LABELS.settingsErrorTitle}
      </EmptyState>
    );
  }
  const data = q.data;

  const setBusy = (id: number, busy: boolean) => setToggling((prev) => {
    const n = new Set(prev);
    if (busy) n.add(id); else n.delete(id);
    return n;
  });
  const toggle = async (r: CancellationReason, next: boolean) => {
    setBusy(r.id, true);
    try {
      await update.mutateAsync({ id: r.id, body: { compensation: next } });
    } catch (e) {
      apiError(e, CANCELLATION_LABELS.toggleFailed);
    } finally {
      setBusy(r.id, false);
    }
  };

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: () => setDeleting(null),
      onError: (e) => { apiError(e, CANCELLATION_LABELS.deleteFailed); setDeleting(null); },
    });
  };

  return (
    <div className="settings-tab">
      <p className="settings-section__lead">{CANCELLATION_LABELS.settingsLead}</p>
      {SIDES.map((side) => {
        const rows = data[side] ?? [];
        const titleId = `cancel-reasons-${side}`;
        return (
          <section key={side} className="settings-section" aria-labelledby={titleId}>
            <div className="settings-section__head">
              <h2 id={titleId} className="settings-section__title">{CANCELLATION_SIDE_LABELS[side]}</h2>
              <button type="button" className="btn-add" onClick={() => setDialog({ side, editing: null })}>
                {CANCELLATION_LABELS.addReason}
              </button>
            </div>
            <div className="table-panel">
              <div className="table-wrap">
                <table className="settings-table settings-table--reasons">
                  <thead>
                    <tr>
                      <th>{CANCELLATION_LABELS.reason}</th>
                      <th>{CANCELLATION_LABELS.compensation}</th>
                      <th aria-label={CANCELLATION_LABELS.rowActions} />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id}>
                        <td className="settings-note">{r.name}</td>
                        <td className="settings-toggle-cell">
                          <Switch
                            checked={Boolean(r.compensation)}
                            onChange={(next) => { void toggle(r, next); }}
                            disabled={toggling.has(r.id)}
                            aria-label={`${CANCELLATION_LABELS.compensation}: ${r.name}`}
                          />
                        </td>
                        <td>
                          <ActionMenu
                            label={CANCELLATION_LABELS.rowActions}
                            items={[
                              { label: CANCELLATION_LABELS.edit, onSelect: () => setDialog({ side, editing: r }) },
                              { label: CANCELLATION_LABELS.remove, danger: true, onSelect: () => setDeleting(r) },
                            ]}
                          />
                        </td>
                      </tr>
                    ))}
                    {/* Системная строка: есть всегда, не редактируется, компенсации нет по правилу. */}
                    <tr>
                      <td className="settings-note">{CANCELLATION_LABELS.otherReason}</td>
                      <td className="settings-muted">{CANCELLATION_LABELS.noCompensation}</td>
                      <td />
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        );
      })}

      {dialog && (
        <CancellationReasonDialog side={dialog.side} editing={dialog.editing} onClose={() => setDialog(null)} />
      )}
      {deleting && (
        <ConfirmModal
          title={CANCELLATION_LABELS.deleteTitle}
          message={CANCELLATION_LABELS.deleteMessage(deleting.name)}
          confirmLabel={CANCELLATION_LABELS.remove}
          danger
          isPending={remove.isPending}
          onConfirm={confirmDelete}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
