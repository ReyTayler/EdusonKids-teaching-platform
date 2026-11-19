import type { Interval } from './types';

/** Сетка — полные сутки: график может начинаться и заканчиваться в любое время (до 24:00). */
export const GRID_START_MIN = 0;
export const SLOT_MIN = 30;
export const SLOT_COUNT = 48;                    // 00:00–24:00
/** Порядок колонок сетки Пн…Вс → day_of_week (Вс=0). */
export const WEEK_DOW = [1, 2, 3, 4, 5, 6, 0] as const;
export const DOW_SHORT: Record<number, string> = { 0: 'Вс', 1: 'Пн', 2: 'Вт', 3: 'Ср', 4: 'Чт', 5: 'Пт', 6: 'Сб' };

export function slotLabel(i: number): string {
  const m = GRID_START_MIN + i * SLOT_MIN;
  // Конец последнего слота — 24:00 (сутки закрываются, а не переходят в 00:00 следующего дня).
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

/** 'HH:MM' → минуты от полуночи ('24:00' → 1440). */
export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

/** Интервалы → маска 48 слотов. */
export function intervalsToSlots(ivs: Interval[]): boolean[] {
  const slots = Array<boolean>(SLOT_COUNT).fill(false);
  for (const [s, e] of ivs) {
    // floor/ceil: невыровненное время не должно давать дробные индексы слотов.
    const from = Math.max(0, Math.floor((toMinutes(s) - GRID_START_MIN) / SLOT_MIN));
    const to = Math.min(SLOT_COUNT, Math.ceil((toMinutes(e) - GRID_START_MIN) / SLOT_MIN));
    for (let i = from; i < to; i++) slots[i] = true;
  }
  return slots;
}

/** Маска → склеенные интервалы. */
export function slotsToIntervals(slots: boolean[]): Interval[] {
  const out: Interval[] = [];
  let start = -1;
  for (let i = 0; i <= SLOT_COUNT; i++) {
    const on = i < SLOT_COUNT && slots[i];
    if (on && start < 0) start = i;
    if (!on && start >= 0) { out.push([slotLabel(start), slotLabel(i)]); start = -1; }
  }
  return out;
}

export function sameSlots(a: boolean[], b: boolean[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

export function formatIntervals(ivs: Interval[]): string {
  return ivs.length ? ivs.map(([s, e]) => `${s}–${e}`).join(', ') : 'выходной';
}

/** Сумма часов по маскам (для «Итого N ч/нед»). */
export function totalHours(masks: boolean[][]): number {
  return masks.reduce((acc, m) => acc + m.filter(Boolean).length, 0) * SLOT_MIN / 60;
}
