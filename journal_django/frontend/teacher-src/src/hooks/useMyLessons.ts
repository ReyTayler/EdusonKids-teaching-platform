import { useQuery } from '@tanstack/react-query';
import { api } from '@shared/lib/api';
import type { MyLessonsResponse } from '../lib/types';

export interface MyLessonsParams {
  page: number;
  pageSize?: number;
  from?: string;
  to?: string;
  group?: string;
}

/**
 * GET /api/lessons[?page&page_size&from&to&group] — история проведённых
 * уроков ТЕКУЩЕГО преподавателя (скоуп по teacher_id — на сервере, teacher_id
 * в запросе не передаётся). placeholderData сохраняет предыдущую страницу
 * при пагинации/фильтрах, чтобы список не мигал. pageSize нужен «Моим
 * урокам» — там выборка идёт целым месяцем одним запросом, без постраничного
 * листания.
 */
export function useMyLessons(params: MyLessonsParams) {
  const qs = new URLSearchParams();
  qs.set('page', String(params.page));
  if (params.pageSize) qs.set('page_size', String(params.pageSize));
  if (params.from) qs.set('from', params.from);
  if (params.to) qs.set('to', params.to);
  if (params.group) qs.set('group', params.group);

  return useQuery<MyLessonsResponse>({
    queryKey: ['myLessons', params],
    queryFn: () => api<MyLessonsResponse>('GET', `/api/lessons?${qs.toString()}`),
    placeholderData: (prev) => prev,
    // Всегда свежее при открытии экрана. Сброс кэша после записи урока есть, но
    // при таймауте запроса он не срабатывает — а LessonForm именно тогда
    // отправляет проверять запись сюда. Запрос дешёвый: два SQL на месяц.
    staleTime: 0,
  });
}
