import { useQuery } from '@tanstack/react-query';
import { api } from '@shared/lib/api';
import type { MyPayrollResponse } from '../lib/types';

/**
 * GET /api/my-payroll?month=YYYY-MM — зарплата ТЕКУЩЕГО преподавателя за месяц
 * с расшифровкой каждой выплаты. Скоуп по teacher_id из JWT — на сервере,
 * teacher_id в запросе не передаётся.
 *
 * placeholderData сохраняет предыдущий месяц на экране, пока грузится
 * следующий: при листании стрелками список и итоги не должны мигать.
 */
export function useMyPayroll(month: string) {
  return useQuery<MyPayrollResponse>({
    queryKey: ['myPayroll', month],
    queryFn: () => api<MyPayrollResponse>('GET', `/api/my-payroll?month=${month}`),
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
}
