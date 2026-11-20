import { memo, useLayoutEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react';
import type { Occurrence } from './types';
import { addDays, sameDay, isoDate } from './lib';
import type { Interval } from '../availability/types';
import { toMinutes } from '../availability/grid';
import { layoutColumn, endTime, mskNowMinutes, type Laid } from './layout';

/** Высота часа — как в WeekGrid. */
const ROW_H = 48;
/** Ширины закреплённых колонок — уходят в CSS через --tw-day-w / --tw-time-w. */
const DAY_COL_W = 52;
const TIME_COL_W = 56;
/** Минимальная ширина одной дорожки и колонки преподавателя (имя должно читаться). */
const LANE_W = 96;
const MIN_TEACHER_COL_W = 120;
/** Шкала дня по умолчанию: 08:00–22:00; ранний/поздний урок расширяет день, чтобы не обрезаться. */
const DAY_START_HOUR = 8;
const DAY_END_HOUR = 22;
const DAY_SHORT = ['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС'];

export interface GridTeacher {
  id: number;
  name: string;
  /**
   * Дополнительный преподаватель (неактивный/служебный): колонка появляется,
   * только если у него есть уроки в видимой неделе, и идёт после основных.
   */
  extra?: boolean;
  /** Подпись для приглушённой шапки («не активен», «служебный»). */
  hint?: string;
}

/** Колонка сетки: преподаватель либо «Без преподавателя» (id = null). */
interface GridCol {
  key: string;
  id: number | null;
  name: string;
  muted: boolean;
  hint?: string;
}

const NO_TEACHER_KEY = 'none';

interface DayBlock {
  c: number;
  date: Date;
  iso: string;
  isToday: boolean;
  /** Первый час диапазона дня (0–23) и число строк-часов: ≥ 14 (шкала 08–22, шире — если урок выходит за границы). */
  startHour: number;
  rows: number;
  /** teacherId → раскладка уроков в колонке. */
  laid: Map<string, Laid[]>;
}

/**
 * Один общий минутный таймер на все линии «сейчас» (а не по таймеру на колонку):
 * запускается с первым подписчиком, останавливается с последним. Перерисовываются
 * только сами линии — сетка не затрагивается.
 */
const nowListeners = new Set<() => void>();
let nowTimer: ReturnType<typeof setInterval> | null = null;
function subscribeNow(cb: () => void): () => void {
  nowListeners.add(cb);
  if (nowTimer == null) nowTimer = setInterval(() => nowListeners.forEach((l) => l()), 60_000);
  return () => {
    nowListeners.delete(cb);
    if (nowListeners.size === 0 && nowTimer != null) {
      clearInterval(nowTimer);
      nowTimer = null;
    }
  };
}

/** Линия «сейчас»: время берёт из общего таймера (useSyncExternalStore, примитив — без лишних рендеров). */
const NowLine = memo(function NowLine({ startMin, bodyH, withDot }: { startMin: number; bodyH: number; withDot: boolean }) {
  const nowMin = useSyncExternalStore(subscribeNow, mskNowMinutes);
  const top = ((nowMin - startMin) / 60) * ROW_H;
  if (top < 0 || top > bodyH) return null;
  return (
    <div className="cal-now-line" style={{ top }} aria-hidden>
      {withDot && <span className="cal-now-dot" />}
    </div>
  );
});

/**
 * Вид «по преподавателям» (admin, «Все преподаватели», неделя): колонки —
 * преподаватели, строки — часы, сгруппированные по дням Пн…Вс. У каждого дня
 * своя шкала: по умолчанию 08:00–22:00 (даже в пустой день), а если урок выходит
 * за эти границы — день расширяется ровно настолько, чтобы урок не обрезался.
 * Слева закреплены день и время, сверху — имена преподавателей.
 */
export function TeacherWeekGrid({
  monday,
  occurrences,
  teachers,
  today,
  onSelect,
  resolveColor,
  renderTeacher,
  availabilityByTeacher,
  dataReady = true,
}: {
  monday: Date;
  occurrences: Occurrence[];
  teachers: GridTeacher[];
  today: Date;
  onSelect: (occ: Occurrence, e: React.MouseEvent | React.KeyboardEvent) => void;
  resolveColor: (occ: Occurrence) => string;
  /** Имя преподавателя в шапке колонки (admin — ссылка на карточку). Для «Без преподавателя» (id = null) не вызывается. */
  renderTeacher?: (name: string, teacherId: number) => ReactNode;
  /** teacherId → дата → окна. Нет ключа у преподавателя — графика нет, фон не рисуем. */
  availabilityByTeacher?: Record<string, Record<string, Interval[]>> | null;
  /** false, пока показаны данные прошлой недели (placeholder): автопрокрутку откладываем до настоящих данных. */
  dataReady?: boolean;
}) {
  const { days, cols, maxLanes } = useMemo(() => {
    // Колонка урока: преподаватель из основного списка или из «дополнительных»
    // (неактивные/служебные — только если у них есть уроки на неделе); остальное
    // (teacherId null / неизвестный id) — «Без преподавателя». Ничего не теряем.
    const main = teachers.filter((t) => !t.extra);
    const extraById = new Map(teachers.filter((t) => t.extra).map((t) => [t.id, t]));
    const mainIds = new Set(main.map((t) => t.id));
    const colKeyOf = (occ: Occurrence): string => {
      const id = occ.teacherId;
      if (id != null && (mainIds.has(id) || extraById.has(id))) return String(id);
      return NO_TEACHER_KEY;
    };

    // Даты видимой недели: в admin окно ответа может быть шире (кэш/соседние недели),
    // а колонки «дополнительных» преподавателей нужны только за эту неделю.
    const weekIsos = new Set(Array.from({ length: 7 }, (_, c) => isoDate(addDays(monday, c))));
    // iso-дата → колонка → уроки.
    const byDate = new Map<string, Map<string, Occurrence[]>>();
    const usedKeys = new Set<string>();
    for (const occ of occurrences) {
      if (!occ.time || !weekIsos.has(occ.date)) continue;
      const key = colKeyOf(occ);
      usedKeys.add(key);
      let perCol = byDate.get(occ.date);
      if (!perCol) byDate.set(occ.date, (perCol = new Map()));
      const arr = perCol.get(key);
      if (arr) arr.push(occ); else perCol.set(key, [occ]);
    }

    const colList: GridCol[] = main.map((t) => ({ key: String(t.id), id: t.id, name: t.name, muted: false }));
    for (const t of extraById.values()) {
      if (usedKeys.has(String(t.id))) colList.push({ key: String(t.id), id: t.id, name: t.name, muted: true, hint: t.hint });
    }
    if (usedKeys.has(NO_TEACHER_KEY)) colList.push({ key: NO_TEACHER_KEY, id: null, name: 'Без преподавателя', muted: true });

    const lanesByCol = new Map<string, number>();
    const blocks: DayBlock[] = Array.from({ length: 7 }, (_, c) => {
      const date = addDays(monday, c);
      const iso = isoDate(date);
      const perCol = byDate.get(iso);
      const laid = new Map<string, Laid[]>();
      let first = Infinity;
      let last = -Infinity;
      if (perCol) {
        for (const list of perCol.values()) {
          for (const o of list) {
            const start = toMinutes(o.time!);
            first = Math.min(first, start);
            last = Math.max(last, start + (o.durationMinutes ?? 60));
          }
        }
      }
      const has = Number.isFinite(first);
      const startHour = has ? Math.min(DAY_START_HOUR, Math.floor(first / 60)) : DAY_START_HOUR;
      const endHour = has ? Math.min(24, Math.max(DAY_END_HOUR, Math.ceil(last / 60))) : DAY_END_HOUR;
      const rows = endHour - startHour;
      if (perCol) {
        for (const [key, list] of perCol) {
          const l = layoutColumn(list, rows * ROW_H, ROW_H, startHour * 60);
          laid.set(key, l);
          lanesByCol.set(key, Math.max(lanesByCol.get(key) ?? 1, ...l.map((x) => x.lanes)));
        }
      }
      return { c, date, iso, isToday: sameDay(date, today), startHour, rows, laid };
    });
    return { days: blocks, cols: colList, maxLanes: lanesByCol };
  }, [monday, occurrences, teachers, today]);

  const gridTemplate = `var(--tw-day-w) var(--tw-time-w) ${cols
    .map((c) => `minmax(${Math.max(MIN_TEACHER_COL_W, (maxLanes.get(c.key) ?? 1) * LANE_W)}px, 1fr)`)
    .join(' ')}`;

  // Автопрокрутка при открытии и смене недели: сегодня в неделе → к «сейчас»
  // (или к началу сегодняшнего дня, если «сейчас» вне его шкалы), иначе — в начало.
  // Время берём напрямую в эффекте, а не из state: минутный тик не должен
  // дёргать прокрутку пользователя.
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const headRef = useRef<HTMLDivElement | null>(null);
  const todayRef = useRef<HTMLDivElement | null>(null);
  const todayBlock = days.find((d) => d.isToday);
  const weekKey = monday.getTime();
  // Подпись раскладки дней: прокрутка срабатывает при реальной смене шкал,
  // а фоновый рефетч с теми же уроками её не дёргает.
  const layoutSig = days.map((d) => `${d.startHour}:${d.rows}`).join();
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    // Пока показаны данные прошлой недели (placeholder), шкалы дней ещё не те —
    // прокручиваем, когда пришли настоящие.
    if (!wrap || !dataReady) return;
    const block = todayRef.current;
    if (!todayBlock || !block) {
      wrap.scrollTop = 0;
      return;
    }
    const headH = headRef.current?.offsetHeight ?? 0;
    const blockTop = block.getBoundingClientRect().top - wrap.getBoundingClientRect().top + wrap.scrollTop;
    const nowOffset = ((mskNowMinutes() - todayBlock.startHour * 60) / 60) * ROW_H;
    if (nowOffset >= 0 && nowOffset <= todayBlock.rows * ROW_H) {
      // Шапка залипает и закрывает headH px вьюпорта — центрируем «сейчас» в оставшейся части.
      wrap.scrollTop = blockTop + nowOffset - (headH + wrap.clientHeight) / 2;
    } else {
      wrap.scrollTop = blockTop - headH;
    }
  }, [weekKey, !!todayBlock, dataReady, layoutSig]); // eslint-disable-line react-hooks/exhaustive-deps -- todayBlock пересоздаётся при каждом пересчёте; важны только неделя и факт «сегодня в неделе»

  return (
    <div
      className="cal-grid-wrap tw-wrap"
      ref={wrapRef}
      style={{ ['--tw-day-w' as any]: `${DAY_COL_W}px`, ['--tw-time-w' as any]: `${TIME_COL_W}px` }}
    >
      <div className="tw-head" ref={headRef} style={{ gridTemplateColumns: gridTemplate }}>
        <div className="tw-corner tw-corner--day" />
        <div className="tw-corner tw-corner--time" />
        {cols.map((c) => (
          <div
            key={c.key}
            className={`tw-teacher-head${c.muted ? ' muted' : ''}`}
            title={c.hint ? `${c.name} (${c.hint})` : c.name}
          >
            <span className="tw-teacher-name">{renderTeacher && c.id != null ? renderTeacher(c.name, c.id) : c.name}</span>
            {c.hint && <span className="tw-teacher-hint">{c.hint}</span>}
          </div>
        ))}
      </div>

      {days.map((d) => (
        <div
          key={d.c}
          ref={d.isToday ? todayRef : undefined}
          className={`tw-day${d.isToday ? ' today' : ''}`}
          style={{ gridTemplateColumns: gridTemplate, ['--row-h' as any]: `${ROW_H}px` }}
        >
          <div className="tw-day-cell">
            <span className="tw-day-name">{DAY_SHORT[d.c]}</span>
            {/* Даты недели строятся в UTC (см. lib.addDays) — берём UTC-число. */}
            <span className="tw-day-date">{d.date.getUTCDate()}</span>
          </div>

          <div className="tw-time-cell" style={{ height: d.rows * ROW_H }}>
            {/* Подпись стоит на линии начала часа; у первого часа линии сверху
                нет — подпись сидит под границей дня. */}
            {Array.from({ length: d.rows }, (_, i) => (
              <div
                key={i}
                className={`cal-hour-label${i === 0 ? ' tw-hour-label--first' : ''}`}
                style={{ top: i === 0 ? 2 : i * ROW_H }}
              >
                {String(d.startHour + i).padStart(2, '0')}:00
              </div>
            ))}
          </div>

          {cols.map((t, ti) => {
            const sched = t.id != null ? availabilityByTeacher?.[String(t.id)] : undefined;
            const windows = sched ? sched[d.iso] ?? [] : null;
            const bodyH = d.rows * ROW_H;
            const startMin = d.startHour * 60;
            return (
              <div
                key={t.key}
                className={`cal-col tw-cell${d.isToday ? ' today' : ''}${sched ? ' has-availability' : ''}`}
                style={{ height: bodyH }}
              >
                {/* Рабочие окна — до блоков уроков, чтобы уроки были поверх. */}
                {windows?.map(([start, end], i) => {
                  const top = Math.max(0, ((toMinutes(start) - startMin) / 60) * ROW_H);
                  const bottom = Math.min(bodyH, ((toMinutes(end) - startMin) / 60) * ROW_H);
                  if (bottom <= top) return null;
                  return (
                    <div
                      key={`av-${i}`}
                      className="cal-avail"
                      style={{ top, height: bottom - top, ['--avail-top' as any]: `${top}px` }}
                      aria-hidden
                    />
                  );
                })}

                {(d.laid.get(t.key) ?? []).map(({ occ, key, top, height, lane, lanes }) => {
                  const dur = occ.durationMinutes ?? 60;
                  // Время показываем, только если занятие не «ровно час в начале часа»:
                  // иначе оно и так читается по линиям сетки.
                  const desc = `${occ.groupDisplay}, ${occ.time}–${endTime(occ.time!, dur)}, ${t.name}${occ.status === 'moved' || occ.status === 'cancelled' ? ` — ${occ.label}` : ''}`;
                  const offGrid = !(occ.time!.endsWith(':00') && dur === 60);
                  return (
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
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault(); // пробел не должен листать контейнер
                          onSelect(occ, e);
                        }
                      }}
                      title={desc}
                      aria-label={desc}
                    >
                      {occ.movedFrom && <span className="cal-moved" title={`Перенесён с ${occ.movedFrom}`} aria-label="перенесён">↪</span>}
                      <div className="lb-title">{occ.groupDisplay}</div>
                      {offGrid && <div className="lb-time">{`${occ.time}–${endTime(occ.time!, dur)}`}</div>}
                    </div>
                  );
                })}

                {d.isToday && <NowLine startMin={startMin} bodyH={bodyH} withDot={ti === 0} />}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
