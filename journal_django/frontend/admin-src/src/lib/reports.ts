// journal_django/frontend/admin-src/src/lib/reports.ts
// Типы раздела «Отчёты». Отчёты НЕ хранятся: генерация в Celery, скачивание
// сразу по готовности по task_id (celery result backend).

export type ReportTaskState = 'PENDING' | 'STARTED' | 'SUCCESS' | 'FAILURE';

export interface ReportRunResponse {
  task_id: string;
}

export interface ReportTaskStatus {
  state: ReportTaskState;
  filename: string | null;
  row_count: number | null;
  error: string | null;
}

export const RENEWALS_MONTH = 'renewals_month';
export const ACCOUNTING_MONTH = 'accounting_month';
export const ATTENDANCE_MONTH = 'attendance_month';
export const REVENUE_FORECAST = 'revenue_forecast';
export const RETENTION = 'retention';
export const STUDENTS_BY_TEACHER = 'students_by_teacher';
export const ACCOUNTING_STUDENTS = 'accounting_students';
export const PAID_THROUGH = 'paid_through';

// Месяцы для селекта.
export const MONTHS_RU = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

/** Дополнительный флажок в карточке отчёта (кроме месяца/года). */
export interface ReportToggleDef {
  key: string;
  label: string;
  hint?: string;
}

/**
 * Описание типа отчёта для UI: заголовок, подпись, опциональные флажки и сборка
 * params(year, month→1..12, значения флажков).
 */
export interface ReportTypeDef {
  reportType: string;
  title: string;
  desc: string;
  toggles?: ReportToggleDef[];
  /** Подпись к селектору месяца, если «за месяц» неточно (у прогноза это старт раскладки). */
  monthLabel?: string;
  /** Отчёт строится на сегодня — выбора месяца и года нет. */
  noPeriod?: boolean;
  buildParams: (
    year: number,
    month: number,
    toggles: Record<string, boolean>,
  ) => Record<string, unknown>;
}

const ym = (year: number, month: number) => `${year}-${String(month).padStart(2, '0')}`;

export const REPORT_TYPES: ReportTypeDef[] = [
  {
    reportType: RENEWALS_MONTH,
    title: 'Отчёт по продлениям',
    desc: 'Промежуточные результаты по статусам сделок (активных и закрытых), затронутых в выбранном месяце.',
    buildParams: (year, month) => ({ year, month }),
  },
  {
    reportType: ACCOUNTING_MONTH,
    title: 'Отчёт по поступлениям и выручке',
    desc: 'Реестр поступлений: каждая оплата, пришедшая в выбранном месяце или '
      + 'признавшая в нём выручку — дата платежа, цена 1 урока, признанная выручка '
      + 'по месяцам, выручка итого, возвраты и остаток аванса на конец месяца.',
    buildParams: (year, month) => ({ month: ym(year, month) }),
  },
  {
    reportType: ACCOUNTING_STUDENTS,
    title: 'Бухгалтерский отчёт',
    desc: 'Снимок финансов на конец выбранного месяца: строка на каждую оплату — '
      + 'посещённые уроки, отработанные деньги, стоимость 1 урока, возврат за месяц, '
      + 'остаток уроков и аванса. Оплата с остатком видна, даже если в месяце по ней '
      + 'ничего не было. '
      + 'Уроки сверх оплаченных — отдельной строкой с долгом. Всё, что датировано '
      + 'позже последнего дня месяца, в отчёт не попадает.',
    buildParams: (year, month) => ({ month: ym(year, month) }),
  },
  {
    reportType: ATTENDANCE_MONTH,
    title: 'Отчёт по посещаемости',
    desc: 'По каждому ученику базы и каждой его группе: даты уроков за месяц и статус «Был» / «Не был» / «Отработал» / «Сгорел».',
    buildParams: (year, month) => ({ month: ym(year, month) }),
  },
  {
    reportType: REVENUE_FORECAST,
    title: 'Прогноз отработки денег',
    desc: 'Неотработанные деньги каждого ученика, разложенные на месяцы вперёд по одному абонементу (4 урока) в месяц. Лист на каждое направление.',
    monthLabel: 'Раскладка с месяца',
    toggles: [
      {
        key: 'full_history',
        label: 'Вся история',
        hint: 'Добавить прошлые месяцы фактом отработки — видно и уже признанную выручку, и прогноз.',
      },
    ],
    buildParams: (year, month, toggles) => ({
      month: ym(year, month),
      full_history: Boolean(toggles.full_history),
    }),
  },
  {
    reportType: RETENTION,
    title: 'Отчёт по переходимости',
    desc: 'По каждому направлению и преподавателю за месяц: сколько детей занималось, '
      + 'сколько продлилось, ушло и зависло — с развёрткой по всем циклам продлений. '
      + 'Цикл = 1 абонемент = 4 урока.',
    buildParams: (year, month) => ({ month: ym(year, month) }),
  },
  {
    reportType: STUDENTS_BY_TEACHER,
    title: 'Ученики по преподавателям',
    desc: 'По каждому ученику и каждой его группе за месяц: направление, основной '
      + 'преподаватель группы, сколько уроков ученик должен был посетить и на скольких '
      + 'был. Неоплачиваемые пропуски из нормы вычитаются. Ученик из двух групп даёт '
      + 'две строки — по одной на преподавателя. 45-минутное занятие = 0,5 урока.',
    buildParams: (year, month) => ({ month: ym(year, month) }),
  },
  {
    reportType: PAID_THROUGH,
    title: 'Оплачено до',
    desc: 'По каждому ученику базы на сегодня: на каком курсе учится сейчас, сколько '
      + 'оплаченных уроков осталось и до какого числа хватит оплаты — по правилу '
      + '«4 урока = 1 месяц». Месяцы считаются у всех, а дата — только у тех, кто сейчас '
      + 'учится: без группы, в заморозке или ушедшим — пусто. Два курса — через запятую.',
    noPeriod: true,
    buildParams: () => ({}),
  },
];

/** Описание отчёта по его типу из адреса страницы (`/admin/reports/:reportType`). */
export function findReportType(reportType: string): ReportTypeDef | undefined {
  return REPORT_TYPES.find((def) => def.reportType === reportType);
}
