import { useQuery } from '@tanstack/react-query';
import { api } from '@shared/lib/api';
import type { GetDataResponse, GetAllDataResponse } from '../lib/types';

/**
 * POST /api/getData — группы ТОЛЬКО текущего преподавателя (для «Мои занятия»).
 * staleTime покрупнее, чем у отчёта: справочник групп/учеников/остатков меняется
 * реже, чем недельное расписание, и после submitLesson инвалидируется явно.
 */
export function useTeacherData() {
  return useQuery<GetDataResponse>({
    queryKey: ['teacherData'],
    queryFn: () => api<GetDataResponse>('POST', '/api/getData'),
    staleTime: 5 * 60_000,
  });
}

/**
 * POST /api/getAllData — свои группы + чужие, где преподавателю назначено ещё не
 * проведённое занятие (вложено по преподавателю-владельцу). Нужен форме записи урока
 * чужой группы; enabled=false, пока такую форму не открыли.
 *
 * staleTime: 0 — список назначенных групп меняет админ («Сменить преподавателя»), и
 * кэш на минуты давал бы ложное «группа не найдена» по только что назначенному
 * занятию. Запрос лёгкий (несколько групп) и идёт только при открытии формы.
 */
export function useAllData(enabled: boolean) {
  return useQuery<GetAllDataResponse>({
    queryKey: ['allData'],
    queryFn: () => api<GetAllDataResponse>('POST', '/api/getAllData'),
    enabled,
    staleTime: 0,
  });
}
