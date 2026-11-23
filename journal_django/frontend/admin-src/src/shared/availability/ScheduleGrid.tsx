import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Occurrence } from '../calendar/types';
import { dayMonth } from '../calendar/lib';
import { DOW_SHORT, GRID_START_MIN, SLOT_COUNT, SLOT_MIN, WEEK_DOW, slotLabel, slotsToIntervals, toMinutes } from './grid';

/** Высота строки (30 минут) — час = 48 px. Держать в синхроне с запасным значением --sched-row-h в schedule.css. */
const ROW_H = 24;
/** Зона у края контейнера, в которой протяжка начинает прокручивать сетку. */
const EDGE = 40;
/** Ширина колонки времени (синхронно с grid-template-columns в schedule.css). */
const GUTTER_W = 56;
/** Максимальная скорость автопрокрутки при протяжке, px за кадр. */
const MAX_SPEED = 16;
/** Куда прокручивать при открытии: не позже 08:00, минус час запаса сверху. */
const DEFAULT_FOCUS_MIN = 8 * 60;

/** Минуты от полуночи по Москве — как и «сегодня» в остальном календаре (МСК = UTC+3 без DST). */
function mskNowMinutes(): number {
  const msk = new Date(Date.now() + 3 * 3600 * 1000);
  return msk.getUTCHours() * 60 + msk.getUTCMinutes();
}
/** Сдвиг пальца, после которого касание считается прокруткой, а не тапом. */
const TAP_SLOP = 10;

export interface ScheduleGridProps {
  /** 7 колонок × 48 слотов — то, что сейчас показано (черновик или база). */
  masks: boolean[][];
  /** Даты колонок (только режим «неделя»); null — шаблон. */
  dates: Date[] | null;
  isoDates: string[] | null;
  todayIso: string;
  overrideDates: Set<string>;
  lessons: Occurrence[];
  /** Правки запрещены: просмотр, загрузка или идёт сохранение. */
  locked: boolean;
  /** Показывать кнопку «вернуть к шаблону» (нужны права на запись). */
  canClear: boolean;
  /** Идёт запрос — повторные действия отключены. */
  busy: boolean;
  onSet: (col: number, slot: number, value: boolean) => void;
  onClearDay: (isoDate: string) => void;
}

interface TapStart { col: number; slot: number; x: number; y: number; id: number }

export function ScheduleGrid({
  masks, dates, isoDates, todayIso, overrideDates, lessons, locked, canClear, busy, onSet, onClearDay,
}: ScheduleGridProps) {
  const [focusCell, setFocusCell] = useState({ col: 0, slot: 18 });
  const [selection, setSelection] = useState<{ col: number; start: number; end: number; value: boolean } | null>(null);
  const painting = useRef(false);
  const paintValue = useRef(true);
  const lastPaint = useRef<{ col: number; slot: number } | null>(null);
  const tap = useRef<TapStart | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const pointer = useRef<{ x: number; y: number; buttons: number; type: string } | null>(null);
  const raf = useRef<number | null>(null);
  const headH0 = useRef(0);
  // Свежие значения для цикла прокрутки: он живёт между рендерами, а замыкание устарело бы.
  const latest = useRef({ onSet, locked, isoDates, todayIso, masks });
  latest.current = { onSet, locked, isoDates, todayIso, masks };

  // Линия «сейчас» — только если на экране текущая неделя; обновляем раз в минуту.
  const [nowMin, setNowMin] = useState(mskNowMinutes);
  useEffect(() => {
    const id = window.setInterval(() => setNowMin(mskNowMinutes()), 60_000);
    return () => window.clearInterval(id);
  }, []);
  const nowCol = isoDates ? isoDates.indexOf(todayIso) : -1;

  // Конец жеста ловим на window: отпускание за пределами сетки тоже должно завершить закраску.
  useEffect(() => {
    const stop = () => { painting.current = false; tap.current = null; lastPaint.current = null; setSelection(null); };
    const move = (e: PointerEvent) => {
      pointer.current = { x: e.clientX, y: e.clientY, buttons: e.buttons, type: e.pointerType };
    };
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    window.addEventListener('pointermove', move);
    return () => {
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      window.removeEventListener('pointermove', move);
      if (raf.current != null) cancelAnimationFrame(raf.current);
      raf.current = null;
    };
  }, []);

  // Автопрокрутка при открытии, смене режима и недели: к началу первого окна (но не позже 08:00) минус час.
  // Ключ — неделя/шаблон: правки черновика прокрутку не сбрасывают.
  const scrollKey = isoDates ? isoDates[0] : 'template';
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    let first = Infinity;
    for (const m of latest.current.masks) {
      const i = m.indexOf(true);
      if (i >= 0) first = Math.min(first, i * SLOT_MIN);
    }
    const target = Math.max(0, Math.min(DEFAULT_FOCUS_MIN, first) - 60);
    el.scrollTop = (target / SLOT_MIN) * ROW_H;
    setFocusCell({ col: Math.max(0, isoDates?.findIndex((d) => d >= todayIso) ?? 0), slot: Math.min(SLOT_COUNT - 1, Math.floor(Math.min(DEFAULT_FOCUS_MIN, first) / SLOT_MIN)) });
  }, [scrollKey]);

  const paintCell = (col: number, slot: number) => {
    const { locked: lk, isoDates: iso, todayIso: today, onSet: set } = latest.current;
    if (lk || (iso && iso[col] < today)) return;
    const previous = lastPaint.current;
    const start = previous?.col === col ? previous.slot : slot;
    // Быстрый жест может перескочить несколько получасовых ячеек.
    for (let i = Math.min(start, slot); i <= Math.max(start, slot); i++) set(col, i, paintValue.current);
    lastPaint.current = { col, slot };
    setSelection((prev) => prev?.col === col ? { ...prev, end: slot } : { col, start: slot, end: slot, value: paintValue.current });
  };

  // Протяжка у края: курсор неподвижен, а сетка уезжает — pointerenter не приходит,
  // поэтому ячейку под курсором находим сами через elementFromPoint.
  const autoScrollLoop = () => {
    raf.current = null;
    const el = wrapRef.current;
    const p = pointer.current;
    if (!painting.current || !el || !p) return;
    if (p.type === 'mouse' && !(p.buttons & 1)) { painting.current = false; return; }
    const rect = el.getBoundingClientRect();
    const headH = headH0.current;
    const topZone = rect.top + headH + EDGE;
    const bottomZone = rect.bottom - EDGE;
    let speed = 0;
    if (p.y < topZone) speed = -Math.min(1, (topZone - p.y) / EDGE) * MAX_SPEED;
    else if (p.y > bottomZone) speed = Math.min(1, (p.y - bottomZone) / EDGE) * MAX_SPEED;
    if (speed !== 0) {
      const before = el.scrollTop;
      el.scrollTop = before + speed;
      if (el.scrollTop !== before) {
        // Курсор мог выйти за край контейнера: зажимаем точку в тело сетки (под шапкой, правее гаттера),
        // иначе elementFromPoint попадёт в шапку/мимо, и ячейки при быстрой прокрутке пропустятся.
        const x = Math.min(Math.max(p.x, rect.left + GUTTER_W + 1), rect.left + el.clientWidth - 1);
        const y = Math.min(Math.max(p.y, rect.top + headH + 1), rect.top + el.clientTop + el.clientHeight - 1);
        const cell = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-col][data-slot]');
        if (cell) {
          const c = Number(cell.dataset.col);
          paintCell(c, Number(cell.dataset.slot));
        }
      }
    }
    raf.current = requestAnimationFrame(autoScrollLoop);
  };
  const startAutoScroll = () => {
    headH0.current = headRef.current?.offsetHeight ?? 0;
    if (raf.current == null) raf.current = requestAnimationFrame(autoScrollLoop);
  };

  const colLocked = (c: number) => locked || (isoDates != null && isoDates[c] < todayIso);

  const bodyH = SLOT_COUNT * ROW_H;

  return (
    <div className="sched-wrap" ref={wrapRef}>
      {selection && <div className="sched-selection" role="status"><span>
        {DOW_SHORT[WEEK_DOW[selection.col]]} {slotLabel(Math.min(selection.start, selection.end))}–{slotLabel(Math.max(selection.start, selection.end) + 1)} · {((Math.abs(selection.end - selection.start) + 1) / 2).toLocaleString('ru-RU')} ч · {selection.value ? 'Рабочее время' : 'Нерабочее время'}
      </span></div>}
      <div className="sched-head" ref={headRef}>
        <div className="sched-corner" />
        {WEEK_DOW.map((dow, c) => {
          const iso = isoDates?.[c];
          const hasOverride = iso != null && overrideDates.has(iso);
          const canReset = canClear && hasOverride && iso! >= todayIso;
          return (
            <div key={dow} className={`sched-col-head${iso === todayIso ? ' is-today' : ''}`}>
              <div className={`sched-col-day${dates ? '' : ' sched-col-day--solo'}`}>{DOW_SHORT[dow]}</div>
              {dates && (
                <>
                  <div className="sched-col-date">{dates[c].getUTCDate()}</div>
                  <div className="sched-col-tools">
                  {hasOverride && (
                    <span className="sched-day-badge" title="Для этой даты действует график, отличный от обычного">Изменён</span>
                  )}
                  {canReset && (
                    <button
                      type="button"
                      className="sched-reset"
                      title="Вернуть обычный график (применится после сохранения)"
                      aria-label={`Вернуть день ${dayMonth(dates[c])} к шаблону`}
                      disabled={busy}
                      onClick={() => onClearDay(iso!)}
                    >
                      ↺
                    </button>
                  )}
                  </div>
                  {iso! < todayIso && <span className="sched-past">Просмотр</span>}
                </>
              )}
            </div>
          );
        })}
      </div>

      {/* Не grid/row: колонки здесь — это дни, а не строки, поэтому честная семантика —
          группа кнопок-переключателей с подробным aria-label. */}
      <div
        className="sched-body"
        role="group"
        aria-label="График работы"
        style={{ ['--sched-row-h' as string]: `${ROW_H}px` }}
      >
        <div className="sched-gutter" aria-hidden="true">
          {Array.from({ length: SLOT_COUNT }, (_, i) =>
            i > 0 && i % 2 === 0 ? (
              <div key={i} className="sched-time" style={{ top: i * ROW_H }}>{slotLabel(i)}</div>
            ) : null,
          )}
        </div>

        {WEEK_DOW.map((dow, c) => {
          const lockedCol = colLocked(c);
          // Отменённые и перенесённые уроки время не занимают — подсказка о них только путает.
          const dayLessons = isoDates
            ? lessons.filter((o) => o.date === isoDates[c] && o.time && o.status !== 'cancelled' && o.status !== 'moved')
            : [];
          return (
            <div key={dow} className="sched-col">
              {masks[c].map((open, i) => (
                <div
                  key={i}
                  role="button"
                  data-col={c}
                  data-slot={i}
                  aria-pressed={open}
                  aria-label={`${DOW_SHORT[dow]}${dates ? ` ${dayMonth(dates[c])}` : ''} ${slotLabel(i)}–${slotLabel(i + 1)}, ${open ? 'рабочее время' : 'нерабочее время'}${lockedCol ? ', только просмотр' : ''}`}
                  title={`${DOW_SHORT[dow]} ${slotLabel(i)}–${slotLabel(i + 1)}`}
                  tabIndex={focusCell.col === c && focusCell.slot === i ? 0 : -1}
                  onFocus={() => setFocusCell({ col: c, slot: i })}
                  onKeyDown={(e) => {
                    if (e.altKey || e.ctrlKey || e.metaKey) return;
                    if (e.key === ' ' || e.key === 'Enter') {
                      e.preventDefault();
                      if (!lockedCol && !e.repeat) onSet(c, i, !open);
                      return;
                    }
                    let col = c, slot = i;
                    if (e.key === 'ArrowUp') slot--;
                    else if (e.key === 'ArrowDown') slot++;
                    else if (e.key === 'ArrowLeft') col--;
                    else if (e.key === 'ArrowRight') col++;
                    else if (e.key === 'Home') slot = 0;
                    else if (e.key === 'End') slot = SLOT_COUNT - 1;
                    else return;
                    e.preventDefault();
                    col = Math.max(0, Math.min(6, col));
                    slot = Math.max(0, Math.min(SLOT_COUNT - 1, slot));
                    const cell = wrapRef.current?.querySelector<HTMLElement>(`[data-col="${col}"][data-slot="${slot}"]`);
                    cell?.focus({ preventScroll: true });
                    const wrap = wrapRef.current;
                    if (wrap && cell) {
                      const top = cell.getBoundingClientRect().top;
                      const bounds = wrap.getBoundingClientRect();
                      const headerH = headRef.current?.offsetHeight ?? 0;
                      if (top < bounds.top + headerH) wrap.scrollTop -= bounds.top + headerH - top;
                      else if (top + ROW_H > bounds.bottom) wrap.scrollTop += top + ROW_H - bounds.bottom;
                    }
                  }}
                  aria-disabled={lockedCol || undefined}
                  className={`sched-cell${open ? ' is-open' : ''}${lockedCol ? ' is-locked' : ''}`}
                  onPointerDown={(e) => {
                    if (lockedCol) return;
                    if (e.pointerType === 'touch') {
                      // Касание может оказаться началом прокрутки — ничего не меняем до pointerup.
                      tap.current = { col: c, slot: i, x: e.clientX, y: e.clientY, id: e.pointerId };
                      return;
                    }
                    if (e.pointerType === 'mouse' && e.button !== 0) return;
                    // Перо неявно захватывает указатель за исходную ячейку — pointerenter соседей
                    // тогда не приходит и протяжка не работает.
                    const el = e.target as Element;
                    if (el.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture?.(e.pointerId);
                    setFocusCell({ col: c, slot: i });
                    (e.currentTarget as HTMLElement).focus({ preventScroll: true });
                    setSelection({ col: c, start: i, end: i, value: !open });
                    lastPaint.current = null;
                    paintValue.current = !open;
                    painting.current = true;
                    pointer.current = { x: e.clientX, y: e.clientY, buttons: e.buttons, type: e.pointerType };
                    paintCell(c, i);
                    startAutoScroll();
                  }}
                  onPointerEnter={(e) => {
                    if (!painting.current || lockedCol) return;
                    // Мышь без нажатой левой кнопки: отпустили за окном или нажали правую —
                    // закраска не должна «залипать».
                    if (e.pointerType === 'mouse' && !(e.buttons & 1)) { painting.current = false; return; }
                    paintCell(c, i);
                  }}
                  onPointerUp={(e) => {
                    const t = tap.current;
                    tap.current = null;
                    if (e.pointerType !== 'touch' || !t || lockedCol) return;
                    if (t.id !== e.pointerId || t.col !== c || t.slot !== i) return;
                    if (Math.hypot(e.clientX - t.x, e.clientY - t.y) >= TAP_SLOP) return;
                    onSet(c, i, !open);
                  }}
                  onPointerCancel={() => { tap.current = null; }}
                />
              ))}
              {slotsToIntervals(masks[c]).map(([start, end]) => (
                <span key={start} className="sched-window-label" aria-hidden="true"
                  style={{ top: (toMinutes(start) / SLOT_MIN) * ROW_H }}>
                  {start}–{end}
                </span>
              ))}
              {dayLessons.map((o, k) => {
                const top = ((toMinutes(o.time!) - GRID_START_MIN) / SLOT_MIN) * ROW_H;
                const h = ((o.durationMinutes ?? 60) / SLOT_MIN) * ROW_H;
                // Обрезаем по границам сетки (сутки 00:00–24:00).
                const t = Math.max(0, top);
                const height = Math.min(bodyH, top + h) - t;
                if (height <= 0) return null;
                const outside = !slotsToIntervals(masks[c]).some(([start, end]) =>
                  toMinutes(start) <= toMinutes(o.time!) && toMinutes(end) >= toMinutes(o.time!) + (o.durationMinutes ?? 60));
                return (
                  <div key={k} className={`sched-lesson${outside ? ' sched-lesson--outside' : ''}`} style={{ top: t, height }}>
                    <span>{o.time} · {o.groupDisplay}</span>
                    {outside && <span className="sched-lesson-warning">Вне рабочих часов</span>}
                  </div>
                );
              })}
              {c === nowCol && (
                <div className="sched-now" style={{ top: (nowMin / SLOT_MIN) * ROW_H }} aria-hidden="true">
                  <span className="sched-now-dot" />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
