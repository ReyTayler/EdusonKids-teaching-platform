/**
 * Клиентская логика записи урока — только ПРЕВЬЮ для UI (номер урока, лимит курса).
 * Сервер — авторитетный источник номера урока и зарплаты (см. ответ /api/submitLesson).
 * Выплату здесь НЕ считаем: её определяет движок зарплат на сервере.
 */
import type { TStudent } from './types';

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
