import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Occurrence } from './types';
import { addDays, sameDay, columnIndexOfIsoDate, isoDate } from './lib';
import type { AvailabilityWindows } from '../availability/types';
import { toMinutes } from '../availability/grid';

/** Высота часа как в Google Calendar. */
const ROW_H = 48;
/** Сетка — полные сутки 00:00–24:00 (24 строки), листается внутри .cal-grid-wrap. */
const HOUR_START = 0;
const HOUR_END = 23;              // последний час-строка (включительно)
/** Если сегодня нет в видимой неделе — открываем сетку на начале рабочего дня. */
const DEFAULT_SCROLL_HOUR = 8;
const N_ROWS = HOUR_END - HOUR_START + 1;
const DAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
/** Минимальная высота блока — 45-минутный урок остаётся читаемым (время+название). */
const MIN_BLOCK_H = 26;
/** Минимальная ширина одной дорожки: день с N пересечениями получает ширину
 *  N×LANE_W (день без плотности растягивается по свободному месту через 1fr),
 *  сетка при переполнении листается по горизонтали — блоки остаются читаемыми. */
const LANE_W = 96;

function topPx(time: string): number {
  const [h, m] = time.split(':').map(Number);
  const hh = Math.min(Math.max(h, HOUR_START), HOUR_END);
  return (hh - HOUR_START) * ROW_H + (m / 60) * ROW_H;
}

/** Граница рабочего окна в px БЕЗ зажима по HOUR_END: окно до 22:00 доходит до низа сетки. */
function windowPx(time: string): number {
  return ((toMinutes(time) - HOUR_START * 60) / 60) * ROW_H;
}

/** 'HH:MM' + длительность (мин) → время окончания 'HH:MM' (диапазон в блоке). */
function endTime(time: string, minutes: number): string {
  const [h, m] = time.split(':').map(Number);
  const total = h * 60 + m + minutes;
  const hh = Math.floor(total / 60) % 24;
  const mm = total % 60;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

interface Laid {
  occ: Occurrence;
  key: string;
  top: number;
  height: number;
  lane: number;
  lanes: number;
}

/**
 * Раскладка колонки дня: высота блока — из durationMinutes (фолбэк 60, admin
 * /plan поле может не отдавать), пересекающиеся занятия делят ширину колонки
 * поровну «дорожками» (как в Google Calendar): кластер пересечений → жадное
 * назначение первой свободной дорожки в порядке начала занятия.
 */
function layoutColumn(occs: Occurrence[], bodyHeight: number): Laid[] {
  const items: Laid[] = occs
    .map((occ, i) => {
      const rawTop = topPx(occ.time!);
      const minutes = occ.durationMinutes ?? 60;
      const raw = (minutes / 60) * ROW_H - 4;
      const height = Math.max(MIN_BLOCK_H, Math.min(raw, bodyHeight - rawTop - 4));
      // Урок у полуночи (23:50 и т.п.): MIN_BLOCK_H мог бы выпихнуть блок ниже
      // сетки — поднимаем top, чтобы top+height не выходил за низ колонки.
      const top = Math.min(rawTop, bodyHeight - height - 2);
      return { occ, key: `${occ.group}-${occ.date}-${occ.time}-${i}`, top, height, lane: 0, lanes: 1 };
    })
    .sort((a, b) => a.top - b.top || b.height - a.height);

  let cluster: Laid[] = [];
  let clusterEnd = -Infinity;

  const flush = () => {
    const laneEnds: number[] = []; // нижняя граница последнего занятия каждой дорожки
    for (const it of cluster) {
      let lane = laneEnds.findIndex((end) => end <= it.top);
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(0);
      }
      it.lane = lane;
      laneEnds[lane] = it.top + it.height;
    }
    for (const it of cluster) it.lanes = laneEnds.length;
    cluster = [];
    clusterEnd = -Infinity;
  };

  for (const it of items) {
    if (cluster.length > 0 && it.top >= clusterEnd) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it.top + it.height);
  }
  if (cluster.length > 0) flush();

  return items;
}

/** Минуты от полуночи ТЕКУЩЕГО времени в МСК (для линии «сейчас»). */
function mskNowMinutes(): number {
  const hhmm = new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date());
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

/** Недельная time-grid (десктоп). Столбец дня = по occ.date, не по монотонной ссылке на report.day. */
export function WeekGrid({
  monday,
  occurrences,
  today,
  onSelect,
  resolveColor,
  availability,
}: {
  monday: Date;
  occurrences: Occurrence[];
  today: Date;
  onSelect: (occ: Occurrence, e: React.MouseEvent | React.KeyboardEvent) => void;
  resolveColor: (occ: Occurrence) => string;
  /** График работы: не null — колонки «закрыты», рабочие окна подсвечены. */
  availability?: AvailabilityWindows;
}) {
  const cols = Array.from({ length: 7 }, (_, c) => {
    const date = addDays(monday, c);
    return { c, date, isToday: sameDay(date, today) };
  });

  const byCol: Occurrence[][] = Array.from({ length: 7 }, () => []);
  for (const occ of occurrences) {
    if (!occ.time) continue;
    byCol[columnIndexOfIsoDate(occ.date)].push(occ);
  }

  const bodyHeight = N_ROWS * ROW_H;

  // Раскладку считаем ОДИН раз на день (нужно число дорожек для ширины колонки).
  const laidByCol = byCol.map((occs) => layoutColumn(occs, bodyHeight));
  // Ширина дня = max(дорожек) × LANE_W (мин. 1), но не уже свободного места (1fr).
  const maxLanes = laidByCol.map((laid) => Math.max(1, ...laid.map((l) => l.lanes), 1));
  const gridTemplate = `56px ${maxLanes.map((n) => `minmax(${n * LANE_W}px, 1fr)`).join(' ')}`;

  // Линия «сейчас»: тик раз в минуту; рисуется только в колонке сегодняшнего
  // дня видимой недели. Сетка суточная, поэтому всегда попадает в диапазон.
  const [nowMin, setNowMin] = useState(() => mskNowMinutes());
  useEffect(() => {
    const t = setInterval(() => setNowMin(mskNowMinutes()), 60_000);
    return () => clearInterval(t);
  }, []);
  const nowTop = ((nowMin - HOUR_START * 60) / 60) * ROW_H;

  // Автопрокрутка при открытии и смене недели: сегодня в неделе → «сейчас» по
  // центру, иначе 08:00. Крутим сам контейнер через scrollTop, а не
  // scrollIntoView — тот прокручивал бы ещё и всю страницу. Текущее время
  // берём напрямую в эффекте, а не из state, чтобы минутный тик не дёргал
  // прокрутку пользователя (эффект зависит только от недели).
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const headRef = useRef<HTMLDivElement | null>(null);
  const hasToday = cols.some((col) => col.isToday);
  const weekKey = monday.getTime();
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const headH = headRef.current?.offsetHeight ?? 0;
    if (hasToday) {
      const y = ((mskNowMinutes() - HOUR_START * 60) / 60) * ROW_H;
      // Шапка залипает сверху и закрывает headH пикселей вьюпорта, поэтому
      // центр видимой области тела сетки смещён на headH/2.
      wrap.scrollTop = y + headH / 2 - wrap.clientHeight / 2;
    } else {
      wrap.scrollTop = (DEFAULT_SCROLL_HOUR - HOUR_START) * ROW_H;
    }
  }, [weekKey, hasToday]);

  return (
    <div className="cal-grid-wrap" ref={wrapRef}>
      <div className="cal-grid-head" ref={headRef} style={{ gridTemplateColumns: gridTemplate }}>
        <div className="cal-col-head cal-corner" />
        {cols.map(({ c, date, isToday }) => (
          <div key={c} className={`cal-col-head${isToday ? ' today' : ''}`}>
            <div className="cal-col-day">{DAY_SHORT[c]}</div>
            {/* Даты недели строятся в UTC (см. lib.addDays) — берём UTC-число. */}
            <div className="cal-col-date">{date.getUTCDate()}</div>
          </div>
        ))}
      </div>

      <div className="cal-grid-body" style={{ ['--row-h' as any]: `${ROW_H}px`, gridTemplateColumns: gridTemplate }}>
        <div className="cal-gutter" style={{ height: bodyHeight }}>
          {/* Подпись стоит на линии начала часа; у 00:00 линии нет — подпись скрыта. */}
          {Array.from({ length: N_ROWS - 1 }, (_, i) => (
            <div key={i} className="cal-hour-label" style={{ top: (i + 1) * ROW_H }}>
              {String(HOUR_START + i + 1).padStart(2, '0')}:00
            </div>
          ))}
        </div>

        {cols.map(({ c, date, isToday }) => (
          <div
            key={c}
            className={`cal-col${isToday ? ' today' : ''}${availability ? ' has-availability' : ''}`}
            style={{ height: bodyHeight }}
          >
            {/* Рабочие окна — до блоков уроков, чтобы уроки были поверх. */}
            {availability && (availability[isoDate(date)] ?? []).map(([start, end], i) => {
              const top = windowPx(start);
              return (
                <div
                  key={`av-${i}`}
                  className="cal-avail"
                  style={{ top, height: windowPx(end) - top, ['--avail-top' as any]: `${top}px` }}
                  aria-hidden
                />
              );
            })}
            {laidByCol[c].map(({ occ, key, top, height, lane, lanes }) => (
              <div
                key={key}
                className={`lb${occ.status === 'overdue' ? ' overdue' : ''}${occ.status === 'done' ? ' done' : ''}${occ.status === 'cancelled' ? ' cancelled' : ''}${occ.isExtra || occ.extraLessonId ? ' makeup' : ''}`}
                style={{
                  top,
                  height,
                  left: `calc(${(lane / lanes) * 100}% + 2px)`,
                  width: `calc(${100 / lanes}% - 5px)`,
                  right: 'auto',
                  ['--subject-color' as any]: resolveColor(occ),
                }}
                onClick={(e) => onSelect(occ, e)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter') onSelect(occ, e); }}
                title={occ.status === 'moved' || occ.status === 'cancelled' ? occ.label : undefined}
              >
                {occ.movedFrom && <span className="cal-moved" title={`Перенесён с ${occ.movedFrom}`} aria-label="перенесён">↪</span>}
                {/* Диапазон времени показываем, когда блок достаточно широкий
                    (≤2 дорожек); в плотных кластерах — только начало, иначе не влезет. */}
                <div className="lb-time">
                  {lanes <= 2 ? `${occ.time}–${endTime(occ.time!, occ.durationMinutes ?? 60)}` : occ.time}
                </div>
                <div className="lb-title">{occ.groupDisplay}</div>
                <div className="lb-meta">
                  {occ.status === 'moved' || occ.status === 'cancelled'
                    ? occ.label
                    : occ.isGroup ? `${occ.students.length} уч.` : occ.teacher}
                </div>
              </div>
            ))}

            {isToday && (
              <div className="cal-now-line" style={{ top: nowTop }} aria-hidden>
                <span className="cal-now-dot" />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
