import { ApiError } from '../../lib/api';
import { fmtRub } from '../../lib/format';
import { PENALTY_MODE_LABELS } from '../../lib/labels';
import type { PersonalRate, RateVersion } from '../../lib/types';

/** '50.00' -> '50', '12.50' -> '12,5': проценты и номера уроков без хвоста нулей. */
export function fmtNum(v: string | null | undefined): string {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return String(n).replace('.', ',');
}

export function cancelText(v: Pick<RateVersion, 'cancel_mode' | 'cancel_value'>): string {
  return v.cancel_mode === 'percent' ? `${fmtNum(v.cancel_value)} % от ставки` : fmtRub(v.cancel_value);
}

export function penaltyText(v: Pick<RateVersion, 'penalty_enabled' | 'penalty_mode' | 'penalty_amount'>): string {
  if (!v.penalty_enabled) return 'выключен';
  return `${fmtRub(v.penalty_amount)} ${PENALTY_MODE_LABELS[v.penalty_mode]}`;
}

export function lessonsRangeText(r: Pick<PersonalRate, 'lesson_from' | 'lesson_to'>): string {
  if (r.lesson_from == null && r.lesson_to == null) return 'все';
  return `${fmtNum(r.lesson_from)}–${fmtNum(r.lesson_to)}`;
}

export function personalValueText(r: Pick<PersonalRate, 'kind' | 'value'>): string {
  if (r.kind === 'fixed') return fmtRub(r.value);
  if (r.kind === 'plus_fixed') return `+${fmtRub(r.value)}`;
  return `+${fmtNum(r.value)} %`;
}

export interface ServerErrors {
  /** Ошибки по полям, которые форма показывает: details.<field>[0]. */
  fields: Record<string, string>;
  /** Ошибки строк направлений: индекс строки в отправленном списке -> текст. */
  directions: Record<number, string>;
  /** Всё остальное (non_field_errors и поля, которых в форме нет) — одним сообщением. */
  general: string;
}

const firstMsg = (v: unknown): string => {
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) {
    for (const x of v) { const m = firstMsg(x); if (m) return m; }
    return '';
  }
  if (v && typeof v === 'object') {
    for (const x of Object.values(v)) { const m = firstMsg(x); if (m) return m; }
  }
  return '';
};

/**
 * Раскладывает 400 `{error, details:{field:[msg]}}`. Поля из `known` попадают к своим
 * полям, вложенный `directions` (список словарей по строкам) — в `directions`, остальное
 * сливается в `general`: ни одна ошибка сервера не должна теряться молча.
 */
export function parseServerErrors(err: unknown, known: string[]): ServerErrors {
  const out: ServerErrors = { fields: {}, directions: {}, general: '' };
  const general: string[] = [];
  if (!(err instanceof ApiError)) {
    out.general = err instanceof Error ? err.message : 'Не удалось сохранить';
    return out;
  }
  const d = err.details;
  if (d && typeof d === 'object') {
    for (const [k, v] of Object.entries(d as Record<string, unknown>)) {
      if (k === 'directions' && Array.isArray(v)) {
        v.forEach((item, i) => { const m = firstMsg(item); if (m) out.directions[i] = m; });
        continue;
      }
      const msg = firstMsg(v);
      if (!msg) continue;
      if (known.includes(k)) out.fields[k] = msg;
      else general.push(msg);
    }
  }
  if (!general.length && !Object.keys(out.fields).length && !Object.keys(out.directions).length) {
    general.push(err.message || 'Не удалось сохранить');
  }
  out.general = general.join(' ');
  return out;
}

/** Сообщение тоста после мутации. */
export function savedMessage(recalculated: number | undefined): string {
  return `Пересчитано начислений: ${recalculated ?? 0}`;
}

/** Дата 'YYYY-MM-DD' + N дней (в UTC, чтобы не зависеть от пояса браузера). */
export function addDaysISO(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

/**
 * Значение числового поля. type="number" всегда отдаёт точку как разделитель
 * (браузер сам разбирает локаль), поэтому здесь только срез пробелов.
 */
export const norm = (s: string): string => s.trim();

/** Число из строки Decimal без хвоста нулей для поля ввода: '600.00' -> '600'. */
export const toField = (v: string | null | undefined): string =>
  (v == null || v === '' ? '' : String(Number(v)));
