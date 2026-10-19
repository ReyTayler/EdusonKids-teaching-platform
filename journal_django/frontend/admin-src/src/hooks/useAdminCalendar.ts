import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { CalendarResponse } from '../shared/calendar/types';

/**
 * GET /api/admin/calendar?[teacher_id=]&from=&to= (role=manager/admin/
 * superadmin) — то же build_calendar(), что и teacher /api/calendar. teacherId
 * null → параметр не передаётся, бэк отдаёт занятия ВСЕХ преподавателей (вся
 * школа); заданный teacherId — фильтр по одному преподавателю.
 */
export function useAdminCalendar(teacherId: number | null, from: string, to: string) {
  return useQuery<CalendarResponse>({
    queryKey: ['admin-calendar', teacherId, from, to],
    queryFn: () => {
      const teacherParam = teacherId != null ? `teacher_id=${teacherId}&` : '';
      return api<CalendarResponse>('GET', `/api/admin/calendar?${teacherParam}from=${from}&to=${to}`);
    },
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
}
