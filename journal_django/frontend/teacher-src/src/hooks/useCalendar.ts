import { useQuery } from '@tanstack/react-query';
import { api } from '@shared/lib/api';
import type { CalendarResponse } from '../lib/types';

/**
 * GET /api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD (роль teacher). Скоуп на
 * ТЕКУЩЕГО преподавателя — на СЕРВЕРЕ (только свои группы), окно ≤92 дней.
 * occurrences привязаны к РЕАЛЬНОЙ дате занятия (occ.date), а не к дню
 * недели+номеру недели, как было в /api/report — поэтому месяц целиком
 * укладывается в один запрос вместо мульти-week. placeholderData сохраняет
 * прошлые данные при перелистывании недели/месяца, чтобы сетка не мигала.
 */
export function useCalendar(from: string, to: string) {
  return useQuery<CalendarResponse>({
    queryKey: ['calendar', from, to],
    queryFn: () => api<CalendarResponse>('GET', `/api/calendar?from=${from}&to=${to}`),
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
}
