import { useEffect, useRef } from 'react';
import type { Occurrence } from '../calendar/types';
import { dayMonth } from '../calendar/lib';
import { DOW_SHORT, GRID_START_MIN, SLOT_COUNT, SLOT_MIN, WEEK_DOW, slotLabel, toMinutes } from './grid';

/** Высота строки (30 минут). Держать в синхроне с запасным значением --sched-row-h в schedule.css. */
const ROW_H = 24;
/** Сдвиг пальца, после которого касание считается прокруткой, а не тапом. */
const TAP_SLOP = 10;

export interface ScheduleGridProps {
  /** 7 колонок × 28 слотов — то, что сейчас показано (черновик или база). */
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
  /** Есть несохранённые правки: сброс дня их не увидит, а следующее «Сохранить» вернёт исключение. */
  dirty: boolean;
  /** Идёт запрос — повторные действия отключены. */
  busy: boolean;
  onSet: (col: number, slot: number, value: boolean) => void;
  onClearDay: (isoDate: string) => void;
}

interface TapStart { col: number; slot: number; x: number; y: number; id: number }

export function ScheduleGrid({
  masks, dates, isoDates, todayIso, overrideDates, lessons, locked, canClear, dirty, busy, onSet, onClearDay,
}: ScheduleGridProps) {
  const painting = useRef(false);
  const paintValue = useRef(true);
  const tap = useRef<TapStart | null>(null);

  // Конец жеста ловим на window: отпускание за пределами сетки тоже должно завершить закраску.
  useEffect(() => {
    const stop = () => { painting.current = false; tap.current = null; };
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    return () => {
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
  }, []);

  const colLocked = (c: number) => locked || (isoDates != null && isoDates[c] < todayIso);

  const bodyH = SLOT_COUNT * ROW_H;

  return (
    <div className="sched-wrap">
      <div className="sched-head">
        <div />
        {WEEK_DOW.map((dow, c) => {
          const iso = isoDates?.[c];
          const hasOverride = iso != null && overrideDates.has(iso);
          const canReset = canClear && hasOverride && iso! >= todayIso;
          return (
            <div key={dow} className={`sched-col-head${iso === todayIso ? ' is-today' : ''}`}>
              <div className="sched-col-day">{DOW_SHORT[dow]}</div>
              {dates && (
                <div className="sched-col-date">
                  {dayMonth(dates[c])}
                  {hasOverride && (
                    <span
                      className="sched-dot"
                      role="img"
                      aria-label="День отличается от шаблона"
                      title="День отличается от шаблона"
                    />
                  )}
                  {canReset && (
                    <button
                      type="button"
                      className="sched-reset"
                      title={dirty ? 'Сначала сохраните или отмените изменения' : 'Вернуть день к шаблону'}
                      aria-label={`Вернуть день ${dayMonth(dates[c])} к шаблону`}
                      disabled={dirty || busy}
                      onClick={() => onClearDay(iso!)}
                    >
                      ↺
                    </button>
                  )}
                </div>
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
            i % 2 === 0 ? (
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
                  aria-pressed={open}
                  aria-label={`${DOW_SHORT[dow]} ${slotLabel(i)}, ${open ? 'открыто' : 'закрыто'}`}
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
                    paintValue.current = !open;
                    painting.current = true;
                    onSet(c, i, paintValue.current);
                  }}
                  onPointerEnter={(e) => {
                    if (!painting.current || lockedCol) return;
                    // Мышь без нажатой левой кнопки: отпустили за окном или нажали правую —
                    // закраска не должна «залипать».
                    if (e.pointerType === 'mouse' && !(e.buttons & 1)) { painting.current = false; return; }
                    onSet(c, i, paintValue.current);
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
              {dayLessons.map((o, k) => {
                const top = ((toMinutes(o.time!) - GRID_START_MIN) / SLOT_MIN) * ROW_H;
                const h = ((o.durationMinutes ?? 60) / SLOT_MIN) * ROW_H;
                // Обрезаем по границам сетки: урок вне окна 08:00–22:00 не должен вылезать.
                const t = Math.max(0, top);
                const height = Math.min(bodyH, top + h) - t;
                if (height <= 0) return null;
                return (
                  <div key={k} className="sched-lesson" style={{ top: t, height }}>
                    {o.groupDisplay}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
