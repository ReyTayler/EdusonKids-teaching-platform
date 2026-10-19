// journal_django/frontend/admin-src/src/pages/sync/SyncActionCard.tsx
import { useState } from 'react';
import { Checkbox } from '../../components/form/Checkbox';
import { useSyncAction } from '../../hooks/useSyncAction';
import type { SyncActionDef } from '../../lib/sync';

export function SyncActionCard({ def }: { def: SyncActionDef }) {
  const [dryRun, setDryRun] = useState(false);
  const { run, isTriggering, status, isPolling, triggerError, statusError } = useSyncAction(def.action);
  const busy = isTriggering || isPolling;
  const errorMessage = triggerError instanceof Error ? triggerError.message
    : statusError instanceof Error ? statusError.message
    : null;

  return (
    <div className="sync-card">
      <div className="sync-card__row">
        <span className="sync-card__label">{def.label}</span>
        {!def.readOnly && (
          <Checkbox
            label="только предпросмотр"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={busy}
          />
        )}
        <button
          type="button"
          className="btn-add"
          disabled={busy}
          onClick={() => run(def.readOnly ? false : dryRun)}
        >
          Запустить
        </button>
      </div>
      {busy && <div className="sync-card__status sync-card__status--pending">Выполняется…</div>}
      {errorMessage && (
        <div className="sync-card__status sync-card__status--error">{errorMessage}</div>
      )}
      {status?.state === 'SUCCESS' && (
        <pre className="sync-card__status sync-card__status--ok">
          {JSON.stringify(status.result, null, 2)}
        </pre>
      )}
      {status?.state === 'FAILURE' && (
        <div className="sync-card__status sync-card__status--error">{status.error}</div>
      )}
    </div>
  );
}
