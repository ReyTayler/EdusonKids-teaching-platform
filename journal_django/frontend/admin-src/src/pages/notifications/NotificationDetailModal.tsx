import { type ReactNode } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { fmtDateTime } from '../../lib/format';
import {
  NOTIFICATION_CHANNEL_LABELS,
  NOTIFICATION_KIND_LABELS,
  NOTIFICATION_STATUS_LABELS,
} from '../../lib/labels';
import type { NotificationRow } from '../../hooks/useNotifications';

/**
 * Тон статуса: доставлено — успех, не доставлено — ошибка, в очереди —
 * приглушённо (ещё ничего не случилось, красить нечего).
 */
const STATUS_TONE: Record<string, string> = {
  sent:   'status-badge--positive',
  failed: 'status-badge--negative',
  queued: 'status-badge--muted',
};

export function NotificationStatusBadge({ status }: { status: string }) {
  return (
    <span className={`status-badge ${STATUS_TONE[status] ?? 'status-badge--muted'}`}>
      {NOTIFICATION_STATUS_LABELS[status] ?? status}
    </span>
  );
}

/** Строка «подпись — значение» в шапке модалки. */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="notification-detail__row">
      <div className="notification-detail__label">{label}</div>
      <div className="notification-detail__value">{children}</div>
    </div>
  );
}

/**
 * Полная карточка сообщения: текст целиком (он многострочный — дайджест на
 * десяток занятий), адресат, статус доставки и причина отказа.
 */
export function NotificationDetailModal({ row, onClose }: {
  row: NotificationRow;
  onClose: () => void;
}) {
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      wide
      title={NOTIFICATION_KIND_LABELS[row.kind] ?? row.kind}
      footer={
        <button type="button" className="btn-cancel" onClick={onClose}>Закрыть</button>
      }
    >
      <div className="notification-detail">
        <div className="notification-detail__meta">
          <Row label="Получатель">
            {row.teacher_name ?? '—'}
            <span className="mono notification-detail__chat">{row.chat_id}</span>
          </Row>
          <Row label="Канал">{NOTIFICATION_CHANNEL_LABELS[row.channel] ?? row.channel}</Row>
          <Row label="Статус">
            <NotificationStatusBadge status={row.status} />
          </Row>
          <Row label="Попыток">{row.attempts}</Row>
          <Row label="Поставлено">{fmtDateTime(row.created_at)}</Row>
          <Row label="Отправлено">{row.sent_at ? fmtDateTime(row.sent_at) : '—'}</Row>
          {row.source_kind && (
            <Row label="Источник">
              <span className="mono">
                {row.source_kind}{row.source_id != null ? ` #${row.source_id}` : ''}
              </span>
            </Row>
          )}
        </div>

        {row.last_error && (
          <div className="notification-detail__error">{row.last_error}</div>
        )}

        <pre className="notification-detail__text">{row.text}</pre>
      </div>
    </Dialog>
  );
}
