/**
 * MSK-date-хелперы + резолвер цвета направления для CalendarView.
 * Копия teacher-src/src/lib/dates.ts + resolveDirectionColor/NO_DIRECTION_COLOR
 * из teacher-src/src/lib/subjects.ts — вынесена сюда, чтобы shared/calendar не
 * тянул зависимость на teacher-src (см. types.ts в этой же папке).
 *
 * Семантика зеркалит бэкенд teacher_spa/views.py::_get_week_start (МСК =
 * UTC+3 без DST; неделя начинается с понедельника). Все «даты недели»
 * представлены как Date в UTC-полночь.
 *
 * teacher-src/src/lib/dates.ts и subjects.ts НЕ удалены — ими пользуется
 * не-календарный teacher-код (ReportPage, MyLessonsPage, LessonForm,
 * GroupsPage). Держать 1 копию логики в двух местах — сознательный
 * компромисс ради изоляции shared от teacher-src (см. промпт задачи).
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

/** Столбец дня недели (0=Пн … 6=Вс) из дня-числа в кодировке 0=Вс…6=Сб (getUTCDay). */
export function columnIndex(reportDay: number): number {
  return reportDay === 0 ? 6 : reportDay - 1;
}

/** 'YYYY-MM-DD' → Date как UTC-полночь (обратная операция к isoDate). */
export function parseIsoDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

/** Столбец дня недели (0=Пн…6=Вс) по ISO-дате занятия (Occurrence.date). */
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

/** Нейтральный цвет для уроков без направления. */
export const NO_DIRECTION_COLOR = 'var(--text3)';

/** Насыщенный красный для карточек доп.урока (ExtraLessonAssignment) — фиксирован,
 * НЕ выводится из направления/группы (в отличие от resolveDirectionColor). */
export const EXTRA_LESSON_COLOR = '#d32f2f';

const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;

/**
 * Точный цвет направления по правилу admin-src/src/lib/direction-color.ts:
 * валидный hex → сам цвет; иначе — детерминированный hue по имени
 * направления (сумма кодов символов % 360, hsl 55%/42%). Если и цвета,
 * и имени нет — нейтральный фолбэк NO_DIRECTION_COLOR.
 */
export function resolveDirectionColor(
  color: string | null | undefined,
  name: string | null | undefined,
): string {
  if (color && HEX_COLOR_RE.test(color)) return color;
  if (name) {
    const hue = [...name].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 360;
    return `hsl(${hue}, 55%, 42%)`;
  }
  return NO_DIRECTION_COLOR;
}
