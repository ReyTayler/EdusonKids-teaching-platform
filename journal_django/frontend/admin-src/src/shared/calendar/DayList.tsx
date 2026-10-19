import type { Occurrence } from './types';
import { StatusPill } from './StatusPill';
import { addDays, dayMonth, sameDay, columnIndexOfIsoDate } from './lib';

const DAY_FULL = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

/** Список уроков по дням (мобильный вид / view=list). Группировка — по occ.date (включая уроки без времени). */
export function DayList({
  monday,
  occurrences,
  today,
  onSelect,
  resolveColor,
}: {
  monday: Date;
  occurrences: Occurrence[];
  today: Date;
  onSelect: (occ: Occurrence, e: React.MouseEvent | React.KeyboardEvent) => void;
  resolveColor: (occ: Occurrence) => string;
}) {
  const byCol: Occurrence[][] = Array.from({ length: 7 }, () => []);
  for (const occ of occurrences) {
    byCol[columnIndexOfIsoDate(occ.date)].push(occ);
  }
  for (const arr of byCol) arr.sort((a, b) => (a.time ?? '99:99').localeCompare(b.time ?? '99:99'));

  return (
    <div className="day-list">
      {Array.from({ length: 7 }, (_, c) => {
        const date = addDays(monday, c);
        const isToday = sameDay(date, today);
        const items = byCol[c];
        return (
          <div key={c} className="day-block">
            <div className={`day-hdr${isToday ? ' today' : ''}`}>
              <span className="day-hdr-name">{DAY_FULL[c]}</span>
              <span className="day-hdr-date">{dayMonth(date)}</span>
              <span className="day-hdr-cnt">{items.length}</span>
            </div>
            {items.length === 0 ? (
              <div className="day-empty">Нет занятий</div>
            ) : (
              items.map((occ, i) => (
                <div
                  key={`${occ.group}-${occ.date}-${occ.time}-${i}`}
                  className={`lrow${occ.status === 'done' ? ' done' : ''}${occ.status === 'cancelled' ? ' cancelled' : ''}${occ.isExtra || occ.extraLessonId ? ' makeup' : ''}`}
                  style={{ ['--subject-color' as any]: resolveColor(occ) }}
                  onClick={(e) => onSelect(occ, e)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') onSelect(occ, e); }}
                >
                  <div className="lrow-time">{occ.time ?? '—'}</div>
                  <div style={{ minWidth: 0 }}>
                    <div className="lrow-title">
                      {occ.movedFrom && <span className="cal-moved" title={`Перенесён с ${occ.movedFrom}`} aria-label="перенесён">↪ </span>}
                      {occ.groupDisplay}
                    </div>
                    <div className="lrow-meta">{occ.isGroup ? `${occ.students.length} уч.` : occ.teacher}</div>
                  </div>
                  <StatusPill status={occ.status} label={occ.label} />
                </div>
              ))
            )}
          </div>
        );
      })}
    </div>
  );
}
