import type { Occurrence } from './types';
import { addDays, isoDate, sameDay, mondayOfWeek } from './lib';

const DAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const MAX_VISIBLE = 3;

/** Месячная сетка (десктоп/планшет) — 6 недель × 7 дней, «хвосты» соседних месяцев приглушены. */
export function MonthGrid({
  monthAnchor,
  lessonsByDate,
  today,
  onSelect,
  resolveColor,
}: {
  /** Первое число видимого месяца (UTC-полночь). */
  monthAnchor: Date;
  /** Дата ('YYYY-MM-DD') → уроки этого дня, уже отфильтрованные по направлению. */
  lessonsByDate: Map<string, Occurrence[]>;
  today: Date;
  onSelect: (occ: Occurrence, e: React.MouseEvent | React.KeyboardEvent) => void;
  resolveColor: (occ: Occurrence) => string;
}) {
  const gridStart = mondayOfWeek(monthAnchor);
  const month = monthAnchor.getUTCMonth();

  const cells = Array.from({ length: 42 }, (_, i) => {
    const date = addDays(gridStart, i);
    return {
      date,
      iso: isoDate(date),
      inMonth: date.getUTCMonth() === month,
      isToday: sameDay(date, today),
    };
  });

  return (
    <div className="month-wrap">
      <div className="month-head">
        {DAY_SHORT.map((d) => (
          <div key={d} className="month-col-head">{d}</div>
        ))}
      </div>
      <div className="month-grid">
        {cells.map((cell) => {
          const items = lessonsByDate.get(cell.iso) ?? [];
          const visible = items.slice(0, MAX_VISIBLE);
          const rest = items.length - visible.length;
          return (
            <div
              key={cell.iso}
              className={`month-cell${cell.inMonth ? '' : ' month-cell--out'}${cell.isToday ? ' month-cell--today' : ''}`}
            >
              <div className="month-daynum">{cell.date.getUTCDate()}</div>
              {visible.length > 0 && (
                <div className="month-chips">
                  {visible.map((occ, i) => (
                    <div
                      key={`${occ.group}-${occ.date}-${occ.time}-${i}`}
                      className={`month-chip${occ.status === 'overdue' ? ' overdue' : ''}${occ.status === 'done' ? ' done' : ''}${occ.status === 'cancelled' ? ' cancelled' : ''}${occ.isExtra || occ.extraLessonId ? ' makeup' : ''}`}
                      style={{ ['--subject-color' as any]: resolveColor(occ) }}
                      onClick={(e) => onSelect(occ, e)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => { if (e.key === 'Enter') onSelect(occ, e); }}
                      title={occ.status === 'moved' || occ.status === 'cancelled' ? occ.label : undefined}
                    >
                      <span className="month-chip-time">{occ.time ?? '—'}</span>
                      <span className="month-chip-title">
                        {occ.movedFrom && <span className="cal-moved" title={`Перенесён с ${occ.movedFrom}`} aria-label="перенесён">↪ </span>}
                        {occ.groupDisplay}
                      </span>
                    </div>
                  ))}
                  {rest > 0 && <div className="month-more">+{rest} ещё</div>}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
