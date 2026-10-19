/**
 * Реэкспорт из shared/calendar (admin-src/src/shared/calendar/Modal.tsx) —
 * компонент вынесен туда вместе с календарём (шаг 6b), но используется и
 * НЕ-календарным teacher-кодом (components/lessons/LessonForm), поэтому
 * импорт из этого файла остаётся рабочим без правок там.
 */
export { Modal } from '@shared/shared/calendar/Modal';
