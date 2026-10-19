/**
 * Реэкспорт из shared/calendar (admin-src/src/shared/calendar/StatusPill.tsx)
 * — компонент вынесен туда вместе с календарём (шаг 6b), но используется и
 * НЕ-календарным teacher-кодом (MyLessonsPage, ReportPage — статусы
 * /api/report и /api/lessons), поэтому импорт из этого файла остаётся
 * рабочим без правок в тех страницах.
 */
export { StatusPill } from '@shared/shared/calendar/StatusPill';
export type { AnyStatus } from '@shared/shared/calendar/StatusPill';
