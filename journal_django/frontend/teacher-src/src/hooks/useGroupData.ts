import { useMemo } from 'react';
import { useTeacherData, useAllData } from './useTeacherData';
import type { GroupData } from '../lib/types';

/**
 * GroupData по имени группы для формы записи урока.
 *
 * Свои группы приходят из /api/getData. Чужой группы там нет — а отметить чужой урок
 * преподаватель вправе, когда админ назначил ему занятие («Сменить преподавателя» /
 * разовая замена): для этого случая догружаем /api/getAllData — сервер отдаёт там
 * свои группы и чужие, где преподавателю назначено ещё не проведённое занятие (не всю
 * школу). Отдельный запрос, а не getAllData всегда, — ради своих групп он не нужен.
 *
 * Общий для календаря и «Моих уроков»: до этого резолв жил только в CalendarPage, и
 * заменяющий преподаватель из «Моих уроков» формы не получал вовсе — там открывался
 * read-only попап, хотя занятие ему назначено.
 *
 * groupName=null (ничего не отмечаем / это доп.урок со своим путём записи) → запросов
 * сверх обычного getData не делаем.
 */
export function useGroupData(groupName: string | null): {
  data: GroupData | null;
  isLoading: boolean;
  isError: boolean;
} {
  const mine = useTeacherData();
  const own = groupName ? (mine.data?.data ?? {})[groupName] : undefined;

  // Чужая группа (замена) — только тогда идём за срезом с назначенными группами.
  const needAll = !!groupName && !own && !mine.isLoading;
  const all = useAllData(needAll);

  const data = useMemo<GroupData | null>(() => {
    if (own) return own;
    if (!groupName || !all.data) return null;
    for (const groups of Object.values(all.data.data)) {
      if (groups[groupName]) return groups[groupName];
    }
    return null;
  }, [own, groupName, all.data]);

  return {
    data,
    // isFetching, а не isLoading: при повторном открытии формы в кэше лежит прежний
    // срез без только что назначенной группы — пока идёт обновление, это «загрузка»,
    // а не «группа не найдена».
    isLoading: !!groupName && !data && (mine.isLoading || (needAll && all.isFetching)),
    isError: mine.isError || (needAll && all.isError),
  };
}
