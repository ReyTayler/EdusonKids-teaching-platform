/**
 * MSK-date-хелперы для навигации по неделям.
 * Семантика зеркалит бэкенд teacher_spa/views.py::_get_week_start
 * (МСК = UTC+3 без DST; неделя начинается с понедельника).
 *
 * Все «даты недели» представлены как Date в UTC-полночь — арифметика ±дни
 * через миллисекунды не даёт DST-сдвигов, а чтение — только через getUTC*.
 */

const DAY_MS = 86_400_000;

const MONTHS_SHORT = [
  'янв', 'фев', 'мар', 'апр', 'мая', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
];

const MONTHS_NOM = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

/** Понедельник ТЕКУЩЕЙ недели по МСК, как UTC-полночь Date. */
export function currentMondayMsk(): Date {
  const msk = new Date(Date.now() + 3 * 3600 * 1000);
  const y = msk.getUTCFullYear();
  const m = msk.getUTCMonth();
  const d = msk.getUTCDate();
  const dow = msk.getUTCDay();            // 0=Вс … 6=Сб
  const daysToMonday = dow === 0 ? 6 : dow - 1;
  return new Date(Date.UTC(y, m, d) - daysToMonday * DAY_MS);
}

/** Дата МСК «сегодня» как UTC-полночь Date (для подсветки текущего дня). */
export function todayMsk(): Date {
  const msk = new Date(Date.now() + 3 * 3600 * 1000);
  return new Date(Date.UTC(msk.getUTCFullYear(), msk.getUTCMonth(), msk.getUTCDate()));
}

/**
 * Возраст в полных годах по дате рождения 'YYYY-MM-DD' (по МСК), или null.
 * Поле age удалено из модели ученика — считаем из даты рождения на клиенте.
 */
export function ageFromBirthDate(birthDate: string | null | undefined): number | null {
  if (!birthDate) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(birthDate).trim());
  if (!m) return null;
  const by = +m[1], bm = +m[2], bd = +m[3];
  const t = todayMsk();
  let age = t.getUTCFullYear() - by;
  const tm = t.getUTCMonth() + 1, td = t.getUTCDate();
  if (tm < bm || (tm === bm && td < bd)) age -= 1;
  return age >= 0 && age < 150 ? age : null;
}

/** «14 лет» / «21 год» / «2 года» по дате рождения, или '' если даты нет. */
export function ageLabel(birthDate: string | null | undefined): string {
  const a = ageFromBirthDate(birthDate);
  if (a == null) return '';
  const mod10 = a % 10, mod100 = a % 100;
  const word = (mod10 === 1 && mod100 !== 11) ? 'год'
    : ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) ? 'года'
    : 'лет';
  return `${a} ${word}`;
}

/** Прибавить n недель (n может быть отрицательным). */
export function addWeeks(monday: Date, n: number): Date {
  return new Date(monday.getTime() + n * 7 * DAY_MS);
}

/** Прибавить n дней. */
export function addDays(base: Date, n: number): Date {
  return new Date(base.getTime() + n * DAY_MS);
}

/** Date → 'YYYY-MM-DD' (по UTC-компонентам). */
export function isoDate(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** Две даты — один и тот же UTC-день? */
export function sameDay(a: Date, b: Date): boolean {
  return a.getUTCFullYear() === b.getUTCFullYear()
    && a.getUTCMonth() === b.getUTCMonth()
    && a.getUTCDate() === b.getUTCDate();
}

/** 'DD.MM' по UTC-компонентам. */
export function dayMonth(d: Date): string {
  return `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

/** Подпись диапазона недели: «29 июн – 5 июл 2026 г.» */
export function weekRangeLabel(monday: Date): string {
  const sunday = addDays(monday, 6);
  const d1 = monday.getUTCDate();
  const d2 = sunday.getUTCDate();
  const m1 = MONTHS_SHORT[monday.getUTCMonth()];
  const m2 = MONTHS_SHORT[sunday.getUTCMonth()];
  const y = sunday.getUTCFullYear();
  if (m1 === m2) return `${d1} – ${d2} ${m2} ${y} г.`;
  return `${d1} ${m1} – ${d2} ${m2} ${y} г.`;
}

/**
 * Столбец дня недели (0=Пн … 6=Вс) из report.day (1=Пн…6=Сб, 0=Вс).
 * Совпадает с _DAY_ORDER на бэкенде.
 */
export function columnIndex(reportDay: number): number {
  return reportDay === 0 ? 6 : reportDay - 1;
}

/** 'YYYY-MM-DD' → Date как UTC-полночь (обратная операция к isoDate). */
export function parseIsoDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

/**
 * Столбец дня недели (0=Пн…6=Вс) по ISO-дате занятия (Occurrence.date).
 * getUTCDay() (0=Вс…6=Сб) совпадает по кодировке с report.day, поэтому
 * переиспользуем columnIndex — эквивалентно «разнице дней от понедельника»
 * в пределах одной недели.
 */
export function columnIndexOfIsoDate(iso: string): number {
  return columnIndex(parseIsoDate(iso).getUTCDay());
}

/** Первое число ТЕКУЩЕГО месяца по МСК, как UTC-полночь Date (для вида «Месяц»). */
export function firstOfMonthMsk(): Date {
  const msk = new Date(Date.now() + 3 * 3600 * 1000);
  return new Date(Date.UTC(msk.getUTCFullYear(), msk.getUTCMonth(), 1));
}

/** Первое число месяца, содержащего дату d (для синхронизации week→month). */
export function monthOf(d: Date): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
}

/** Прибавить n месяцев к «первому числу месяца» (n может быть отрицательным). */
export function addMonths(base: Date, n: number): Date {
  return new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth() + n, 1));
}

/** Понедельник недели, содержащей дату d (для сетки месяца — «хвосты» соседних месяцев). */
export function mondayOfWeek(d: Date): Date {
  const dow = d.getUTCDay();          // 0=Вс … 6=Сб
  const daysToMonday = dow === 0 ? 6 : dow - 1;
  return addDays(d, -daysToMonday);
}

/** Подпись месяца: «Июль 2026». */
export function monthLabel(d: Date): string {
  return `${MONTHS_NOM[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export { DAY_MS };

/** Date → 'YYYY-MM' (по UTC-компонентам) — параметр month у /api/my-payroll. */
export function isoMonth(d: Date): string {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

const DAY_SHORT_MON_FIRST = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/** Короткий день недели даты 'YYYY-MM-DD': «Пн» … «Вс». */
export function weekdayShortOfIso(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return DAY_SHORT_MON_FIRST[(new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7];
}

/** «03.07» из 'YYYY-MM-DD' — год в списке за месяц избыточен. */
export function dayMonthOfIso(iso: string): string {
  return `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
}
