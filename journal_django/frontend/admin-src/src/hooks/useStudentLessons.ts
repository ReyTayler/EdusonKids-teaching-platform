import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Paginated, StudentLessonRow } from '../lib/types';

/** Ключи сортировки, которые принимает эндпоинт (белый список на бэке —
 *  apps/students/lesson_history.py::ORDERING_FIELDS). Мусор даёт 400. */
export type StudentLessonSort = 'submitted_at' | 'lesson_date';

export interface StudentLessonsPeriod {
  /** 'YYYY-MM-DD'; обе границы включительно. Без периода — вся история. */
  dateFrom?: string;
  dateTo?: string;
}

/**
 * GET /api/admin/students/:id/lessons — серверно-пагинированный список уроков
 * ученика. Сортировка — по дате занятия или по дате сохранения, в обе стороны;
 * по умолчанию свежезаполненное сверху. Период необязателен: вкладка карточки
 * показывает всю историю, модалка дашборда — выбранный диапазон.
 */
export function useStudentLessons(
  studentId: number,
  page: number,
  pageSize: number,
  sortBy: StudentLessonSort,
  sortDir: 'asc' | 'desc',
  period: StudentLessonsPeriod = {},
) {
  const qs = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort_by: sortBy,
    sort_dir: sortDir,
  });
  if (period.dateFrom) qs.set('date_from', period.dateFrom);
  if (period.dateTo) qs.set('date_to', period.dateTo);
  const query = qs.toString();

  return useQuery({
    queryKey: ['students', 'lessons', studentId, query],
    queryFn: () =>
      api<Paginated<StudentLessonRow>>(
        'GET', `/api/admin/students/${studentId}/lessons?${query}`,
      ),
    placeholderData: keepPreviousData,
  });
}
