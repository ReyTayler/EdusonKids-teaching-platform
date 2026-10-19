/**
 * Клиентская бизнес-логика записи урока — порт из старого `frontend/teacher/app.js`.
 * ВАЖНО: это только ПРЕВЬЮ для UI. Сервер — авторитетный источник выплаты/номера
 * урока/штрафа (см. ответ /api/submitLesson). Здесь ничего не должно расходиться
 * с бэкендом настолько, чтобы вводить преподавателя в заблуждение, но окончательный
 * расчёт всегда идёт на сервере.
 */
import type { TStudent } from './types';

export const RATES = {
  halfLesson: 250,
  smallFull: 500,
  smallPartial: 300,
  perStudent: 200,
} as const;

/** Выплата за урок (превью). total — размер группы, present — сколько пришло. */
export function calcPayment(total: number, present: number, isHalf: boolean): number {
  if (present === 0) return 0;
  if (isHalf) return RATES.halfLesson * present;
  if (total <= 2) return present === total ? RATES.smallFull : RATES.smallPartial;
  return RATES.perStudent * present;
}

/**
 * «45 минут» в названии группы → полурок (0.5 вместо 1 в счётчике уроков).
 * LEGACY-фолбэк (Ф4): первичный источник — lessonDurationMinutes из
 * /api/group-directions (useGroupDirections). Используется только пока карта
 * направлений ещё не загрузилась или группы в ней нет.
 */
export function isHalfLesson(groupName: string): boolean {
  return /45\s*минут/i.test(groupName);
}

/** Номер следующего урока по уже пройденным (максимум среди учеников группы). */
export function lessonNumber(
  students: Pick<TStudent, 'lessonsDone'>[],
  isHalf: boolean,
): { done: number; step: number; next: number } {
  const step = isHalf ? 0.5 : 1;
  const done = students.length ? Math.max(...students.map((s) => s.lessonsDone ?? 0)) : 0;
  const next = Math.round((done + step) * 10) / 10;
  return { done, step, next };
}

/**
 * Лимит уроков по курсу (эвристика по названию группы). null — лимита нет/неизвестен.
 * LEGACY-фолбэк (Ф4): первичный источник — totalLessons из /api/group-directions
 * (useGroupDirections). Используется только пока карта направлений ещё не
 * загрузилась или группы в ней нет.
 */
export function getCourseLimit(groupName: string): number | null {
  const n = (groupName || '').toLowerCase();
  if (/python/.test(n)) return 56;
  if (/minecraft/.test(n)) return 48;
  if (/roblox/.test(n)) return 40;
  if (/blend|блендер/.test(n)) return 16;
  if (/scratch/.test(n)) return 32;
  if (/веб.?диз|web.?диз|web.?des|веб.?des/.test(n)) return 36;
  if (/веб.?разр|web.?разр|web.?dev|веб.?dev/.test(n)) return 36;
  return null;
}

/** Целое → без дробной части, иначе один знак после запятой (совпадает с app.js). */
export function fmtNum(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** Рубли с разделителями разрядов ru-локали. */
export function rub(n: number): string {
  return n.toLocaleString('ru') + ' ₽';
}
