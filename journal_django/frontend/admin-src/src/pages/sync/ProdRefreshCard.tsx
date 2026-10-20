// journal_django/frontend/admin-src/src/pages/sync/ProdRefreshCard.tsx
import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { useApiError } from '../../hooks/useApiError';
import { useProdRefresh } from '../../hooks/useProdRefresh';
import { fmtDateTime } from '../../lib/format';

/**
 * «Данные с прода»: мягкое обновление из старой системы Eduson
 * (scripts/refresh_from_prod.sh --soft). Карточки нет, если кнопка выключена флагом.
 */
export function ProdRefreshCard() {
  const qc = useQueryClient();
  const showError = useApiError();
  const { status, start } = useProdRefresh();
  const [confirming, setConfirming] = useState(false);
  const data = status.data;
  const running = Boolean(data?.running) || start.isPending;

  // Обновление закончилось — данные групп, учеников и уроков на других экранах устарели.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && data && !data.running) void qc.invalidateQueries();
    wasRunning.current = Boolean(data?.running);
  }, [data, qc]);

  if (!data?.enabled) return null;

  const onConfirm = async () => {
    try {
      await start.mutateAsync();
    } catch (err) {
      showError(err);
    }
    setConfirming(false);
  };

  return (
    <>
      <div className="sync-page__group-title">Данные старой системы</div>
      <div className="sync-card">
        <div className="sync-card__row">
          <div className="sync-card__label">
            Обновить данные с прода
            <div className="sync-card__hint">
              Подтягивает из старой системы учеников, группы, уроки и отзывы. Учётки, продления,
              задачи и настройки не трогаются. Уроки и отзывы, записанные в платформе по группам
              старой системы, заменяются данными оттуда.
            </div>
          </div>
          <button
            type="button"
            className="btn-add"
            disabled={running}
            onClick={() => setConfirming(true)}
          >
            {running ? 'Идёт…' : 'Обновить'}
          </button>
        </div>

        {running && (
          <div className="sync-card__status sync-card__status--pending">
            Обновление идёт{data.started_at ? ` с ${fmtDateTime(data.started_at)}` : ''} — обычно 1–3 минуты.
          </div>
        )}
        {!running && data.last && (
          <div className={`sync-card__status sync-card__status--${data.last.ok ? 'ok' : 'error'}`}>
            {data.last.ok ? 'Последнее обновление прошло' : 'Последнее обновление не удалось'} ({data.last.at}):{' '}
            {data.last.text}
          </div>
        )}
        {data.log_tail.length > 0 && (
          <pre className="sync-card__log">{data.log_tail.join('\n')}</pre>
        )}

        {confirming && (
          <ConfirmModal
            title="Обновить данные с прода?"
            message="Ученики, группы, уроки и отзывы будут обновлены из старой системы. Уроки и отзывы,
              записанные в платформе по её группам, заменятся. Учётки, продления и настройки останутся."
            confirmLabel="Обновить"
            isPending={start.isPending}
            onConfirm={onConfirm}
            onClose={() => setConfirming(false)}
          />
        )}
      </div>
    </>
  );
}
