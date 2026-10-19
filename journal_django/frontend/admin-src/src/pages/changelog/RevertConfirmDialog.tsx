import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Dialog } from '../../components/ui/Dialog';
import { useToast } from '../../components/ui/Toast';
import { useApiError } from '../../hooks/useApiError';
import { useChangelogMutations } from '../../hooks/useChangelog';
import { EntityChips } from '../../components/changelog/EntityChips';
import { ApiError } from '../../lib/api';
import { fmtDateTime } from '../../lib/format';
import { CHANGELOG_ENTITY_LABELS, CHANGELOG_OPERATION_LABELS } from '../../lib/labels';
import type { ChangelogOperation, RevertConflictItem } from '../../lib/types';

const CONFLICT_REASON_LABELS: Record<RevertConflictItem['reason'], string> = {
  row_exists:        'запись уже существует',
  row_missing:       'запись удалена',
  changed_later:     'запись изменена позже этой операции',
  no_previous_state: 'нет предыдущего состояния',
};

export function RevertConfirmDialog({ op, onClose }: {
  op: ChangelogOperation;
  onClose: () => void;
}) {
  const muts = useChangelogMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [conflicts, setConflicts] = useState<RevertConflictItem[] | null>(null);

  const opLabel = CHANGELOG_OPERATION_LABELS[op.operation] ?? op.operation;

  const onConfirm = async () => {
    try {
      const result = await muts.revert.mutateAsync(op.id);
      toast(`Операция отменена (изменений откачено: ${result.reverted_events})`, 'ok');
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const details = err.details as { conflicts?: RevertConflictItem[] } | undefined;
        // Модалка не закрывается — переключаемся в режим конфликта.
        setConflicts(details?.conflicts ?? []);
        return;
      }
      showError(err);
      onClose();
    }
  };

  if (conflicts) {
    return (
      <Dialog
        open
        onOpenChange={(o) => !o && onClose()}
        title="Откат невозможен: конфликт"
        footer={
          <button type="button" className="btn-add" onClick={onClose}>
            Понятно
          </button>
        }
      >
        <p style={{ marginBottom: 'var(--space-3)' }}>
          Эти записи менялись <strong>после</strong> данной операции. Сначала откатите более
          поздние операции по ним (история записи — по ссылкам ниже), затем повторите откат.
        </p>
        <ul style={{ display: 'grid', gap: 'var(--space-2)', listStyle: 'none', padding: 0 }}>
          {conflicts.map((c, i) => {
            const entityLabel = CHANGELOG_ENTITY_LABELS[c.entity] ?? c.entity;
            return (
              <li key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                <span className="status-badge status-badge--negative">
                  {entityLabel} #{String(c.obj_id ?? '?')}
                </span>
                <span style={{ color: 'var(--text2)' }}>
                  {CONFLICT_REASON_LABELS[c.reason] ?? c.reason}
                  {c.fields?.length ? ` (${c.fields.join(', ')})` : ''}
                </span>
                {c.obj_id != null && (
                  <Link
                    className="btn-link"
                    to={`/admin/changelog?entity=${encodeURIComponent(c.entity)}&entity_id=${encodeURIComponent(String(c.obj_id))}`}
                    onClick={onClose}
                  >
                    история записи
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      </Dialog>
    );
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Откатить операцию?"
      footer={
        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <button type="button" className="btn-cancel" onClick={onClose} disabled={muts.revert.isPending}>
            Отмена
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={() => { void onConfirm(); }}
            disabled={muts.revert.isPending}
          >
            {muts.revert.isPending ? 'Откатываем…' : 'Откатить'}
          </button>
        </div>
      }
    >
      <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
        <div><strong>{opLabel}</strong> — {op.summary}</div>
        <div style={{ color: 'var(--text2)' }}>
          {fmtDateTime(op.occurred_at)}
          {op.actor ? <> · <span className="mono">{op.actor.email}</span></> : null}
        </div>
        <EntityChips entities={op.entities} max={6} />
        <p style={{ color: 'var(--text2)', marginTop: 'var(--space-2)' }}>
          Будет отменено изменений: <strong>{op.events_total}</strong>. Откат необратим,
          но сама эта операция тоже попадёт в журнал изменений.
        </p>
      </div>
    </Dialog>
  );
}
