import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { groupPlanKey } from './useGroupPlanCalendar';
import type { GroupScheduleData } from '../lib/types';

const KEY = (groupId: number) => ['group-schedule', groupId] as const;

export function useGroupSchedule(groupId: number) {
  return useQuery({
    queryKey: KEY(groupId),
    queryFn: () => api<GroupScheduleData>('GET', `/api/admin/groups/${groupId}/schedule`),
    enabled: Number.isFinite(groupId) && groupId > 0,
  });
}

export interface ScheduleChangePayload {
  effective_from: string;
  slots: { day_of_week: number; start_time: string }[];
}

export function useScheduleChange(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScheduleChangePayload) =>
      api<GroupScheduleData>('POST', `/api/admin/groups/${groupId}/schedule-change`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY(groupId) });
      // Слоты группы видны и в списке групп (GroupsListPage/GroupFormModal) — сбрасываем тоже.
      qc.invalidateQueries({ queryKey: ['groups'] });
      // apply_schedule_change автогенерирует план (planned_lessons) — обновляем
      // план и календарь группы (тот же ключ).
      qc.invalidateQueries({ queryKey: groupPlanKey(groupId) });
    },
  });
}

