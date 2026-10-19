import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Occurrence } from '../../lib/types';
import { isoDate, todayMsk } from '../../lib/dates';

/**
 * Контекстное меню занятия в календаре (по клику на ячейку): Отметить урок /
 * Открыть карточку группы / Перейти в чат / Подробности. Позиция — точка
 * клика с клампом в вьюпорт; закрытие — клик мимо или Escape.
 *
 * «Отметить урок»: скрыт для done/cancelled (заполнять нечего); задизейблен
 * с подписью для будущей даты (occ.date > сегодня МСК, сравнение по дню, без
 * учёта времени начала) — занятие ещё не наступило; иначе активен.
 * «Перейти в чат» неактивен без groups.vk_chat (occ.vkChat).
 */
export function OccurrenceMenu({
  occ,
  x,
  y,
  onSubmitLesson,
  onOpenGroup,
  onDetails,
  onClose,
}: {
  occ: Occurrence;
  x: number;
  y: number;
  onSubmitLesson: () => void;
  onOpenGroup: () => void;
  onDetails: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: x, top: y });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({
      left: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - r.height - 8)),
    });
  }, [x, y]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const todayIso = isoDate(todayMsk());
  const isFillable = occ.status !== 'done' && occ.status !== 'cancelled';
  const isFuture = occ.date > todayIso;
  const chat = occ.vkChat || null;

  return (
    <div ref={ref} className="occ-menu" style={{ left: pos.left, top: pos.top }} role="menu" aria-label={occ.groupDisplay}>
      <div className="occ-menu-head">
        <span className="occ-menu-title">{occ.groupDisplay}</span>
        <span className="occ-menu-sub">{occ.time ?? 'время не указано'}</span>
      </div>
      {isFillable && (
        <button
          type="button"
          className="occ-menu-item"
          role="menuitem"
          disabled={isFuture}
          title={isFuture ? 'Занятие ещё не наступило' : undefined}
          onClick={onSubmitLesson}
        >
          {occ.extraLessonId != null ? 'Провести доп.урок' : 'Отметить урок'}
          {isFuture && <span className="occ-menu-item-hint">доступно в день урока</span>}
        </button>
      )}
      {occ.extraLessonId == null && (
        <button type="button" className="occ-menu-item" role="menuitem" onClick={onOpenGroup}>
          Открыть карточку группы
        </button>
      )}
      <button
        type="button"
        className="occ-menu-item"
        role="menuitem"
        disabled={!chat}
        title={chat ? undefined : 'У группы не указана ссылка на чат'}
        onClick={() => {
          if (!chat) return;
          window.open(chat, '_blank', 'noopener');
          onClose();
        }}
      >
        Перейти в чат группы
      </button>
      <button type="button" className="occ-menu-item" role="menuitem" onClick={onDetails}>
        Подробности
      </button>
    </div>
  );
}
