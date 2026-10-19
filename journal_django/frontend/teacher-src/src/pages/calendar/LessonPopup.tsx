/**
 * Реэкспорт из shared/calendar (admin-src/src/shared/calendar/LessonPopup.tsx)
 * — компонент вынесен туда вместе с календарём (шаг 6b), но используется и
 * НЕ-календарным teacher-кодом (ReportPage, MyLessonsPage — попап деталей
 * занятия вне CalendarView), поэтому импорт из этого файла остаётся рабочим
 * без правок в тех страницах.
 */
export { LessonPopup } from '@shared/shared/calendar/LessonPopup';
