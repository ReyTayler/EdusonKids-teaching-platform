import { useMemo, useState } from 'react';
import { Combobox, type Option } from '../../components/form/Combobox';
import { TelegramIcon } from '../../components/ui/icons';
import { useToast } from '../../components/ui/Toast';
import { useApiError } from '../../hooks/useApiError';
import { useAuth } from '../../hooks/useAuth';
import { useTeacherTelegramMutations, useTelegramAccounts } from '../../hooks/useNotifications';
import { canWriteTeacherTelegram, type Role } from '../../lib/permissions';
import type { Teacher } from '../../lib/types';

/**
 * Вторая строка пункта: ник и — если аккаунт занят — чей он.
 * Раньше всё это склеивалось в label одной строкой, из-за чего подпись
 * не влезала и налезала на соседний пункт.
 */
function optionHint(username: string | null, boundTo: string | null): string | undefined {
  const parts: string[] = [];
  if (username) parts.push(`@${username}`);
  if (boundTo) parts.push(`занят: ${boundTo}`);
  return parts.length ? parts.join(' · ') : undefined;
}

/** Текущее состояние привязки словами — то, ради чего человек сюда смотрит. */
function TelegramStatus({ telegram }: { telegram: Teacher['telegram'] }) {
  if (!telegram) {
    return <span className="tg-card__status">не привязан</span>;
  }
  if (!telegram.is_active) {
    return (
      <span className="tg-card__status tg-card__status--error">
        заблокировал бота{telegram.blocked_reason ? `: ${telegram.blocked_reason}` : ''}
      </span>
    );
  }
  return <span className="tg-card__status tg-card__status--ok">привязан</span>;
}

/**
 * Блок «Telegram» в шапке карточки преподавателя.
 *
 * Выбор из списка аккаунтов, а не ввод ника: Bot API умеет писать только по
 * числовому chat_id, которого человек не видит, а набор руками даёт опечатки.
 * Список — те, кто уже написал боту /start (иначе привязывать нечего).
 *
 * Менеджеру блок показывается только на чтение: право на POST/DELETE у бэка
 * admin-only (ReadStaffWriteAdmin), кнопка с гарантированным 403 бесполезна.
 */
export function TeacherTelegramBlock({ teacher }: { teacher: Teacher }) {
  const { me } = useAuth();
  const canWrite = canWriteTeacherTelegram(me?.role as Role);
  // Список аккаунтов тянем только тому, кто может привязывать: эндпоинт
  // закрыт для менеджера, и без этого условия его карточка преподавателя
  // ловила бы 403 на каждом открытии.
  const { data, isLoading } = useTelegramAccounts(canWrite);
  const muts = useTeacherTelegramMutations(teacher.id);
  const { toast } = useToast();
  const showError = useApiError();

  const current = teacher.telegram?.chat_id != null ? String(teacher.telegram.chat_id) : '';
  const [selected, setSelected] = useState(current);

  // Свободные аккаунты — наверх: занятый выбирают редко, а листать до своего
  // через полтора десятка чужих привязок приходилось каждый раз.
  const options: Option[] = useMemo(() => {
    const rows = (data?.rows ?? []).map((a) => {
      // Свою же привязку «занятой» не считаем: это не конфликт.
      const boundTo = a.bound_to === teacher.name ? null : a.bound_to;
      return {
        value: String(a.chat_id),
        label: a.full_name,
        hint: optionHint(a.username, boundTo),
        muted: Boolean(boundTo),
      };
    });
    return [...rows.filter((o) => !o.muted), ...rows.filter((o) => o.muted)];
  }, [data, teacher.name]);

  const dirty = selected !== current;
  const busy = muts.link.isPending || muts.unlink.isPending;

  const handleSave = async () => {
    try {
      await muts.link.mutateAsync(Number(selected));
      toast('Telegram привязан', 'ok');
    } catch (err) { showError(err, 'Не удалось привязать Telegram'); }
  };

  const handleUnlink = async () => {
    try {
      await muts.unlink.mutateAsync();
      setSelected('');
      toast('Telegram отвязан', 'ok');
    } catch (err) { showError(err, 'Не удалось отвязать Telegram'); }
  };

  return (
    <div className="tg-card">
      <div className="tg-card__head">
        <span className="tg-card__title">
          <TelegramIcon size={14} />
          Telegram
        </span>
        <TelegramStatus telegram={teacher.telegram} />
      </div>

      {teacher.telegram && (
        <div className="tg-card__account">
          {teacher.telegram.username
            ? `@${teacher.telegram.username}`
            : teacher.telegram.full_name}
        </div>
      )}

      {canWrite && (
        <>
          <Combobox
            value={selected}
            onChange={setSelected}
            options={options}
            placeholder={isLoading ? 'Загрузка…' : 'Выберите аккаунт'}
            itemHeight={52}
            aria-label="Telegram-аккаунт преподавателя"
          />
          <div className="tg-card__actions">
            <button
              type="button"
              className="btn-save"
              onClick={() => { void handleSave(); }}
              disabled={busy || !selected || !dirty}
            >
              {teacher.telegram ? 'Сменить' : 'Привязать'}
            </button>
            {teacher.telegram && (
              <button
                type="button"
                className="btn-cancel"
                onClick={() => { void handleUnlink(); }}
                disabled={busy}
              >
                Отвязать
              </button>
            )}
          </div>
          {!isLoading && options.length === 0 && (
            <div className="tg-card__hint">
              Боту ещё никто не писал: аккаунт появится в списке после команды /start.
            </div>
          )}
        </>
      )}
    </div>
  );
}
