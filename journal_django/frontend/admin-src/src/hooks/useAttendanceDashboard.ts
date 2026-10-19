import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type {
  AttendanceDashboardRow,
  AttendanceDashboardSummary,
  Paginated,
} from '../lib/types';

const BASE = '/api/admin/reports/attendance-dashboard';

/** Ключи сортировки списка (белый список на бэке; мусор даёт 400). */
export type AttendanceSort = 'billed' | 'full_name';

export interface AttendanceDashboardParams {
  dateFrom: string;
  dateTo: string;
}

/**
 * Две цифры экрана. Отдельный запрос от списка: так сводка не пересчитывается
 * при листании и не реагирует на фильтр по имени — она про всю школу.
 */
export function useAttendanceDashboardSummary(p: AttendanceDashboardParams) {
  return useQuery({
    queryKey: ['attendance-dashboard', 'summary', p.dateFrom, p.dateTo],
    queryFn: () =>
      api<AttendanceDashboardSummary>(
        'GET', `${BASE}/summary?date_from=${p.dateFrom}&date_to=${p.dateTo}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export interface AttendanceStudentsParams extends AttendanceDashboardParams {
  page: number;
  pageSize: number;
  sortBy: AttendanceSort;
  sortDir: 'asc' | 'desc';
  nameQuery: string;
}

/** Серверно-пагинированный список учеников с разбивкой по типам занятий. */
export function useAttendanceDashboardStudents(p: AttendanceStudentsParams) {
  const qs = new URLSearchParams({
    date_from: p.dateFrom,
    date_to: p.dateTo,
    page: String(p.page),
    page_size: String(p.pageSize),
    sort_by: p.sortBy,
    sort_dir: p.sortDir,
  });
  if (p.nameQuery) qs.set('filter[full_name]', p.nameQuery);
  const query = qs.toString();

  return useQuery({
    queryKey: ['attendance-dashboard', 'students', query],
    queryFn: () => api<Paginated<AttendanceDashboardRow>>('GET', `${BASE}/students?${query}`),
    placeholderData: keepPreviousData,
  });
}
