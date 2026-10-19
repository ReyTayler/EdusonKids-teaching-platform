import { useCallback, useMemo, useState } from 'react';
import { useCalendar } from '../../hooks/useCalendar';
import type { OccStatus, Occurrence } from '../../lib/types';
import { StatusPill } from '../../components/ui/StatusPill';
import { LessonPopup } from '../calendar/LessonPopup';
import { PageHead } from '../../components/ui/PageHead';
import { resolveDirectionColor } from '../../lib/subjects';
import {
  currentMondayMsk, todayMsk, addWeeks, isoDate, sameDay, weekRangeLabel,
  addDays, dayMonth, columnIndexOfIsoDate,
} from '../../lib/dates';

type Filter = 'all' | OccStatus | 'notime';

const FILTERS: { key: Filter; label: string; cls?: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'done', label: 'Заполнено', cls: 'done' },
  { key: 'overdue', label: 'Надо заполнить', cls: 'overdue' },
  { key: 'pending', label: 'Ещё не было' },
  { key: 'moved', label: 'Перенесены' },
  { key: 'cancelled', label: 'Отменены' },
  { key: 'notime', label: 'Без времени' },
];

const DAY_FULL = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

export default function ReportPage() {
  const [monday, setMonday] = useState(() => currentMondayMsk());
  const [filter, setFilter] = useState<Filter>('all');
  const [selected, setSelected] = useState<Occurrence | null>(null);

  const today = todayMsk();
  const isCurrentWeek = sameDay(monday, currentMondayMsk());

  /** /api/calendar скоупит на текущего преподавателя на сервере — окно ровно текущей недели. */
  const from = isoDate(monday);
  const to = isoDate(addDays(monday, 6));
  const { data, isLoading, isError, isFetching } = useCalendar(from, to);
  const occAll = data?.occurrences ?? [];

  /** Цвет левой рамки строки — прямо из occurrence (direction/color), карта направлений не нужна. */
  const resolveColor = useCallback(
    (occ: Occurrence): string => resolveDirectionColor(occ.color, occ.direction ?? occ.group),
    [],
  );

  const stats = useMemo(() => {
    let done = 0, overdue = 0, pending = 0;
    for (const occ of occAll) {
      if (occ.status === 'done') done++;
      else if (occ.status === 'overdue') overdue++;
      else if (occ.status === 'pending') pending++;
    }
    return { done, overdue, pending };
  }, [occAll]);

  const filtered = useMemo(() => {
    if (filter === 'all') return occAll;
    if (filter === 'notime') return occAll.filter((o) => !o.time);
    return occAll.filter((o) => o.status === filter);
  }, [occAll, filter]);

  const byCol: Occurrence[][] = Array.from({ length: 7 }, () => []);
  for (const occ of filtered) {
    byCol[columnIndexOfIsoDate(occ.date)].push(occ);
  }
  for (const arr of byCol) arr.sort((a, b) => (a.time ?? '99:99').localeCompare(b.time ?? '99:99'));
  const hasAny = byCol.some((a) => a.length);

  return (
    <div className="rep-page">
      <PageHead title="Отчёт по уроку" sub={isFetching ? <span className="t-updating">обновление…</span> : undefined}>
        <div className="cal-week-nav">
          <button className="cal-nav-btn" onClick={() => setMonday((m) => addWeeks(m, -1))} aria-label="Предыдущая неделя">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
          </button>
          <span className="cal-week-label">{weekRangeLabel(monday)}</span>
          <button className="cal-nav-btn" onClick={() => setMonday((m) => addWeeks(m, 1))} aria-label="Следующая неделя">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>
          {!isCurrentWeek && <button className="cal-today-btn" onClick={() => setMonday(currentMondayMsk())}>Сегодня</button>}
        </div>
      </PageHead>

      <div className="rstat-bar">
        <div className="rstat"><div className="rstat-num ok">{stats.done}</div><div className="rstat-lbl">Заполнено</div></div>
        <div className="rstat"><div className="rstat-num warn">{stats.overdue}</div><div className="rstat-lbl">Надо заполнить</div></div>
        <div className="rstat"><div className="rstat-num mut">{stats.pending}</div><div className="rstat-lbl">Ещё не было</div></div>
      </div>

      <div className="rf-chips">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`rf-chip${filter === f.key ? ' active' : ''}${f.cls ? ` ${f.cls}` : ''}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="cal-skel cal-skel--md" />
      ) : isError ? (
        <div className="cal-error">Не удалось загрузить отчёт.</div>
      ) : !hasAny ? (
        <div className="cal-empty">Ничего не найдено по выбранному фильтру.</div>
      ) : (
        <div className="day-list">
          {Array.from({ length: 7 }, (_, c) => {
            const items = byCol[c];
            if (items.length === 0) return null;
            const date = addDays(monday, c);
            const isToday = sameDay(date, today);
            return (
              <div key={c} className="day-block">
                <div className={`day-hdr${isToday ? ' today' : ''}`}>
                  <span className="day-hdr-name">{DAY_FULL[c]}</span>
                  <span className="day-hdr-date">{dayMonth(date)}</span>
                  <span className="day-hdr-cnt">{items.length}</span>
                </div>
                {items.map((occ, i) => (
                  <div
                    key={`${occ.group}-${occ.date}-${occ.time}-${i}`}
                    className={`lrow${occ.status === 'cancelled' ? ' cancelled' : ''}`}
                    style={{ ['--subject-color' as any]: resolveColor(occ) }}
                    onClick={() => setSelected(occ)}
                    role="button" tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') setSelected(occ); }}
                  >
                    <div className="lrow-time">{occ.time ?? '—'}</div>
                    <div className="lrow-body">
                      <div className="lrow-title">{occ.groupDisplay}</div>
                      <div className="lrow-meta">{occ.isGroup ? `${occ.students.length} уч.` : occ.teacher}</div>
                    </div>
                    <StatusPill status={occ.status} label={occ.label} />
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}

      {selected && <LessonPopup lesson={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
