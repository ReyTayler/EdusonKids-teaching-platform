import { useMemo } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Direction, Group, Teacher } from '../lib/types';
import type { Occurrence, OccStatus } from '../shared/calendar/types';

/** Сериализуемая плановая строка — форма ответа GET /api/admin/groups/<id>/plan
 * (заморожена; см. apps/scheduling/repository.py::_plan_row_dict). */
export interface PlanRow {
  id: number;
  seq: number | null;
  lesson_number: number | null;
  scheduled_date: string;        // 'YYYY-MM-DD'
  scheduled_time: string | null; // 'HH:MM'
  teacher_id: number | null;
  teacher_name: string | null;
  status: OccStatus;
  fact_lesson_id: number | null;
  fact_date: string | null;      // фактическая дата проведения ('YYYY-MM-DD') или null
  record_url: string | null;     // ссылка на запись урока (из факта) или null
  moved_from_date: string | null;
  is_extra: boolean;
}

/** Экспортируется — useGroupPlan.ts (мутации операций плана) инвалидирует по тому же ключу. */
export const groupPlanKey = (groupId: number) => ['group-plan', groupId] as const;

/** Готовые подписи статуса — зеркало apps/scheduling/services.py::_LABELS
 * (тот же текст, что видит преподаватель в /api/calendar). */
const STATUS_LABELS: Record<OccStatus, string> = {
  done: 'Заполнено',
  overdue: 'Надо заполнить',
  pending: 'Пока урока не было',
  cancelled: 'Отменён',
  moved: 'Перенесён',
};

function dayOfWeek(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0=Вс…6=Сб
}

/**
 * GET /api/admin/groups/<id>/plan — список плановых занятий группы (весь
 * план разом, без окна дат: /plan не принимает from/to). placeholderData
 * сохраняет прошлые строки при быстром переключении между группами.
 */
export function useGroupPlan(groupId: number) {
  return useQuery({
    queryKey: groupPlanKey(groupId),
    queryFn: () => api<PlanRow[]>('GET', `/api/admin/groups/${groupId}/plan`),
    enabled: Number.isFinite(groupId) && groupId > 0,
    placeholderData: keepPreviousData,
  });
}

/**
 * Мапит строки /plan в Occurrence-форму, которую понимает CalendarView
 * (shared/calendar). Известные ограничения (сознательный компромисс —
 * не трогаем бэкенд ради этого шага, см. отчёт):
 *  - students всегда [] — /plan не отдаёт состав группы (это делает
 *    GroupMembersBlock/вкладка «Ученики»); счётчик «N уч.» будет 0.
 *  - movedTo всегда null — /plan отдаёт только moved_from_date, поэтому
 *    строка «Перенесён: X → Y» в LessonPopup для статуса moved не покажется
 *    (label всё равно корректен: «Перенесён»).
 */
export function useGroupPlanCalendar(
  group: Group | undefined,
  direction: Direction | null,
  teachers: Teacher[],
) {
  const groupId = group?.id ?? 0;
  const { data: rows, isLoading, isError, isFetching } = useGroupPlan(groupId);

  const occurrences = useMemo<Occurrence[]>(() => {
    if (!group || !rows) return [];
    const groupTeacherName = teachers.find((t) => t.id === group.teacher_id)?.name ?? null;
    const isGroup = !group.is_individual;
    const isHalf = group.lesson_duration_minutes === 45;

    return rows.map((r): Occurrence => {
      const rowTeacherName = r.teacher_name ?? groupTeacherName ?? '—';
      const isOverride = group.teacher_id != null && r.teacher_id != null && r.teacher_id !== group.teacher_id;
      return {
        id: r.id,
        group: group.name,
        groupDisplay: group.name,
        teacher: isOverride ? (groupTeacherName ?? rowTeacherName) : rowTeacherName,
        teacherOverride: isOverride ? rowTeacherName : null,
        direction: direction?.name ?? null,
        color: direction?.color ?? null,
        isGroup,
        date: r.scheduled_date,
        time: r.scheduled_time,
        day: dayOfWeek(r.scheduled_date),
        seq: r.seq,
        lessonNumber: r.lesson_number,
        isHalf,
        isExtra: r.is_extra,
        status: r.status,
        label: STATUS_LABELS[r.status] ?? '',
        movedFrom: r.moved_from_date,
        movedTo: null,
        students: [],
      };
    });
  }, [rows, group, direction, teachers]);

  return { occurrences, isLoading, isError, isFetching };
}
