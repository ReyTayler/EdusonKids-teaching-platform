// journal_django/frontend/admin-src/src/pages/notifications/NotificationToggleBar.tsx
import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { useApiError } from '../../hooks/useApiError';
import { fmtDateTime } from '../../lib/format';
import { useNotificationToggle, useSetNotificationToggle } from '../../hooks/useNotifications';

/**
 * Общешкольный выключатель рассылки. Рисуется над вкладками раздела и виден
 * на обеих — это состояние не «Журнала» и не «Расписания», а всей отправки
 * целиком (каникулы, инцидент, разъехавшиеся данные).
 *
 * Семантика выключения — полная тишина: пока выключено, сообщения не
 * создаются вовсе (ни точечные, ни дайджесты), уже стоящие в очереди не
 * отправляются, а при включении обратно ничего не досылается задним числом —
 * о событиях за время паузы преподаватели просто не узнают. Поэтому включение
 * происходит сразу, а выключение требует подтверждения: случайный клик
 * заставит замолчать сотню людей.
 */
/**
 * Колокольчик — включено. Тот же контур, что у пункта меню «Уведомления»
 * (пункт навигации): раздел и его выключатель должны читаться как одно.
 */
function BellIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}

/** Перечёркнутый колокольчик — выключено. */
function BellOffIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      <path d="M18.63 13A17.89 17.89 0 0 1 18 8" />
      <path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14" />
      <path d="M18 8a6 6 0 0 0-9.33-5" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}

export function NotificationToggleBar() {
  const { data } = useNotificationToggle();
  const setToggle = useSetNotificationToggle();
  const showError = useApiError();
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Пока состояние не загрузилось — не показываем ни чекбокс, ни плашку:
  // мигание «выключено → включено» на каждом заходе в раздел хуже, чем пауза.
  if (!data) return null;

  const apply = (value: boolean) => {
    setToggle.mutate(value, { onError: (err) => showError(err) });
  };

  return (
    <div className="notif-toggle">
      <div className="notif-toggle__row">
        <button
          type="button"
          className={`notif-switch${data.is_enabled ? '' : ' notif-switch--off'}`}
          // aria-pressed вместо роли переключателя: для скринридера это кнопка
          // с состоянием, а перечёркнутый колокольчик остаётся чисто визуальным.
          aria-pressed={data.is_enabled}
          disabled={setToggle.isPending}
          title={data.is_enabled
            ? 'Выключить рассылку уведомлений'
            : 'Включить рассылку уведомлений'}
          onClick={() => {
            // Включение — сразу. Выключение — только после подтверждения:
            // случайный клик заставит замолчать сотню человек.
            if (data.is_enabled) setConfirmOpen(true);
            else apply(true);
          }}
        >
          {data.is_enabled ? <BellIcon /> : <BellOffIcon />}
          <span>{data.is_enabled ? 'Уведомления включены' : 'Уведомления выключены'}</span>
        </button>
        {data.updated_by && (
          <span className="notif-toggle__meta">
            изменил(а) {data.updated_by} · {fmtDateTime(data.updated_at)}
          </span>
        )}
      </div>

      {!data.is_enabled && (
        <div className="notif-toggle__banner" role="alert">
          Рассылка выключена: уведомления преподавателям не создаются и не отправляются —
          ни точечные, ни дайджесты расписания и незаполненных отчётов. О событиях за время
          паузы преподаватели не узнают: при включении обратно накопившееся задним числом
          разослано не будет.
        </div>
      )}

      {confirmOpen && (
        <Dialog
          open
          onOpenChange={(o) => { if (!o) setConfirmOpen(false); }}
          title="Выключить рассылку?"
          footer={
            <>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmOpen(false)}
                disabled={setToggle.isPending}
              >
                Отмена
              </button>
              <button
                type="button"
                className="btn-danger"
                disabled={setToggle.isPending}
                onClick={() => {
                  apply(false);
                  setConfirmOpen(false);
                }}
              >
                {setToggle.isPending ? 'Выключаем…' : 'Выключить'}
              </button>
            </>
          }
        >
          <p>
            Уведомления преподавателям перестанут создаваться — ни точечные сообщения об
            изменениях, ни дайджесты расписания и незаполненных отчётов. Сообщения, которые
            должны были уйти за время паузы, разосланы не будут — ни сейчас, ни задним числом
            после включения обратно.
          </p>
        </Dialog>
      )}
    </div>
  );
}
