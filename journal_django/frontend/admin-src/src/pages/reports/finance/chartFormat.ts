import { MONTHS_RU } from '../../../lib/slots';

/** Короткий формат оси сумм: 380000 → "380K" */
export function fmtAxisRub(v: number | string): string {
  const n = Number(v);
  return Math.abs(n) >= 1000 ? `${Math.round(n / 1000)}K` : String(n);
}

/** '2026-09-18' → '18.09.26' */
export function fmtDayTick(iso: string): string {
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y.slice(2)}`;
}

/** '2026-09' → "Сен '26" */
export function fmtMonthTick(ym: string): string {
  const [y, m] = ym.split('-');
  return `${MONTHS_RU[Number(m) - 1].slice(0, 3)} '${y.slice(2)}`;
}

/** '2026-09' → 'Сентябрь 2026' */
export function fmtMonthFull(ym: string): string {
  const [y, m] = ym.split('-');
  return `${MONTHS_RU[Number(m) - 1]} ${y}`;
}

/** Общие свойства подписей осей обоих графиков периода. */
/** Оси — mono 11px --text3 (Eduson). */
export const AXIS_TICK = { fill: 'var(--text3)', fontSize: 11, fontFamily: 'var(--font-mono)' };
