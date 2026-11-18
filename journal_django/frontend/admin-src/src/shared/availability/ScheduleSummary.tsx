import { dayMonth, parseIsoDate } from '../calendar/lib';
import { DOW_SHORT, WEEK_DOW, formatIntervals, slotsToIntervals, totalHours } from './grid';
import type { ScheduleTemplate } from './types';

function fmtHours(h: number): string {
  return Number.isInteger(h) ? String(h) : h.toFixed(1);
}

export interface ScheduleSummaryProps {
  masks: boolean[][];
  overrides: ScheduleTemplate['upcoming_overrides'];
  canEdit: boolean;
  busy: boolean;
  /** Есть несохранённые правки — удаление исключения заблокировано (иначе «Сохранить» вернёт его). */
  dirty: boolean;
  onRemoveOverride: (date: string) => void;
}

export function ScheduleSummary({ masks, overrides, canEdit, busy, dirty, onRemoveOverride }: ScheduleSummaryProps) {
  return (
    <aside className="sched-summary">
      <div className="sched-sum-title">Сводка</div>
      <ul className="sched-sum-list">
        {WEEK_DOW.map((dow, c) => (
          <li key={dow}>
            <span className="sched-sum-dow">{DOW_SHORT[dow]}</span>
            <span>{formatIntervals(slotsToIntervals(masks[c]))}</span>
          </li>
        ))}
      </ul>
      <div className="sched-sum-total">Итого {fmtHours(totalHours(masks))} ч/нед</div>

      <div className="sched-sum-title">Исключения</div>
      {overrides.length === 0 ? (
        <div className="sched-sum-empty">Нет</div>
      ) : (
        <ul className="sched-sum-list">
          {overrides.map((o) => {
            const d = parseIsoDate(o.date);
            return (
              <li key={o.date}>
                <span>
                  {dayMonth(d)} ({DOW_SHORT[d.getUTCDay()]}) — {formatIntervals(o.intervals)}
                </span>
                {canEdit && (
                  <button
                    type="button"
                    className="sched-x"
                    aria-label={`Удалить исключение ${dayMonth(d)}`}
                    title={dirty ? 'Сначала сохраните или отмените изменения' : 'Удалить исключение'}
                    disabled={dirty || busy}
                    onClick={() => onRemoveOverride(o.date)}
                  >
                    ✕
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
