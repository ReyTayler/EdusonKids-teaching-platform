/** Интервал окна графика: ['HH:MM', 'HH:MM'], шаг 30 мин, сутки 00:00–24:00 (конец окна может быть '24:00'). */
export type Interval = [string, string];

/** GET …/work-schedule/template */
export interface ScheduleTemplate {
  /** Ключи '0'…'6', конвенция Вс=0 (JS getDay). */
  days: Record<string, Interval[]>;
  /** Исключения с сегодняшнего дня; intervals: [] — день закрыт. */
  upcoming_overrides: { date: string; intervals: Interval[] }[];
}

/** GET …/work-schedule?from=&to= */
export interface ScheduleRange {
  has_schedule: boolean;
  windows: Record<string, Interval[]>;   // 'YYYY-MM-DD' → окна
  override_dates: string[];
}

/** Поле availability ответа календаря: null — графика нет / не один преподаватель. */
export type AvailabilityWindows = Record<string, Interval[]> | null;
