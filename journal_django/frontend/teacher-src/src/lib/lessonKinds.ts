import type { MyLessonStudentStatus, PayrollLessonKind } from './types';

/**
 * Подпись типа урока. Обычный урок бейджа не получает — это шум.
 * Общая для «Зарплаты» и «Моих уроков»: списки двух экранов совпадают строка в
 * строку, и один и тот же урок обязан называться в них одинаково.
 */
export const LESSON_KIND_LABEL: Partial<Record<PayrollLessonKind | 'cancellation', string>> = {
  substitution: 'Замена',
  reschedule: 'Перенос',
  extra: 'Доп. занятие',
  burned: 'Сгоревшее занятие',
  // Оплаченная отмена — только в «Зарплате»; в «Моих уроках» таких строк нет.
  cancellation: 'Отмена занятия',
};

/** Подпись статуса ученика в истории «Мои уроки». */
export const STUDENT_STATUS_LABEL: Record<MyLessonStudentStatus, string> = {
  present: 'был',
  free: 'был, бесплатно',
  absent: 'не был',
  skip: 'не посещает',
  burned: 'пропуск сгорел',
};
