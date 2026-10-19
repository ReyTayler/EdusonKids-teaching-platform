/**
 * Мутации операций плана (planned_lessons) — шаг 7 материализованных плановых
 * уроков (docs/lesson-scheduling.md). Стиль зеркалит useGroupSchedule.ts:
 * useMutation + invalidate (без optimistic-апдейтов — не нужны для admin-форм).
 *
 * Все эндпоинты — POST /api/admin/groups/<id>/plan/*, RBAC IsManagerOrAdmin,
 * X-CSRFToken ставит сам api() (см. lib/api.ts). Инвалидируем group-plan
 * (тот же ключ, что useGroupPlanCalendar → useGroupPlan использует для чтения)
 * и ['groups'] (слоты видны и в списке групп).
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { groupPlanKey } from './useGroupPlanCalendar';
import { groupPlanHealthKey } from './useGroupPlanHealth';
import type { PlanRow } from './useGroupPlanCalendar';

function invalidatePlan(qc: ReturnType<typeof useQueryClient>, groupId: number) {
  qc.invalidateQueries({ queryKey: groupPlanKey(groupId) });
  // Слоты (recurrence-шаблон) меняются при permanent-change — видны в списке групп.
  qc.invalidateQueries({ queryKey: ['groups'] });
  // «Состояние плана» на той же вкладке держит ПРЕДПРОСМОТР починки. Без сброса
  // блок остаётся с диффом от прежнего состояния и активной кнопкой на уже
  // несуществующих изменениях: данные защищает рукопожатие expected (409), но
  // пользователь ловил бы непонятную ошибку вместо актуальной картины.
  qc.invalidateQueries({ queryKey: groupPlanHealthKey(groupId) });
}

/** POST /plan/generate — сгенерировать план курса (идемпотентно, без тела). */
export function useGeneratePlan(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<PlanRow[]>('POST', `/api/admin/groups/${groupId}/plan/generate`),
    onSuccess: () => invalidatePlan(qc, groupId),
  });
}

export interface ReschedulePayload {
  new_date: string;
  new_time?: string | null;
  new_teacher_id?: number | null;
}

/** POST /plan/<lid>/reschedule — разовый перенос (+опц. время/преподаватель). */
export function useReschedule(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ lessonId, body }: { lessonId: number; body: ReschedulePayload }) =>
      api<PlanRow>('POST', `/api/admin/groups/${groupId}/plan/${lessonId}/reschedule`, body),
    onSuccess: () => invalidatePlan(qc, groupId),
  });
}

export interface PermanentChangeSlot {
  day_of_week: number; // Вс=0..Сб=6
  start_time: string;  // HH:MM
}

export interface PermanentChangePayload {
  from_seq: number;
  effective_from: string; // 'YYYY-MM-DD' — дата, с которой действует новое расписание
  new_slots: PermanentChangeSlot[]; // набор из 1..N слотов (мультислот + расширение 1→N)
  new_teacher_id?: number | null;
}

/** Один разовый оператор расписания в хвосте, который будет сброшен при
 * реальном применении permanent-change (см. preview ниже). */
export interface AffectedOp {
  kind: 'cancellation' | 'reschedule' | 'substitution';
  seq?: number;
  date: string;
  time?: string;
  from_date?: string;
}

/** POST /plan/permanent-change — перенос навсегда с позиции from_seq. */
export function usePermanentChange(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PermanentChangePayload) =>
      api<PlanRow[]>('POST', `/api/admin/groups/${groupId}/plan/permanent-change`, body),
    onSuccess: () => invalidatePlan(qc, groupId),
  });
}

/** POST /plan/permanent-change с preview:true — read-only, ничего не пишет и не
 * инвалидирует кэш; возвращает список разовых операций хвоста, которые
 * будут сброшены, если применить это изменение по-настоящему. */
export function usePermanentChangePreview(groupId: number) {
  return useMutation({
    mutationFn: (body: PermanentChangePayload) =>
      api<{ affected: AffectedOp[] }>(
        'POST',
        `/api/admin/groups/${groupId}/plan/permanent-change`,
        { ...body, preview: true },
      ),
  });
}

/** POST /plan/<lid>/change-teacher — разовая смена преподавателя одной строки. */
export function useChangeTeacher(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ lessonId, newTeacherId }: { lessonId: number; newTeacherId: number }) =>
      api<PlanRow>('POST', `/api/admin/groups/${groupId}/plan/${lessonId}/change-teacher`, {
        new_teacher_id: newTeacherId,
      }),
    onSuccess: () => invalidatePlan(qc, groupId),
  });
}

export interface ChangeTeacherPermanentPayload {
  from_seq: number;
  new_teacher_id: number;
}

/** POST /plan/change-teacher-permanent — смена преподавателя хвоста (seq>=from_seq). */
export function useChangeTeacherPermanent(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ChangeTeacherPermanentPayload) =>
      api<PlanRow[]>('POST', `/api/admin/groups/${groupId}/plan/change-teacher-permanent`, body),
    onSuccess: () => invalidatePlan(qc, groupId),
  });
}

/** POST /plan/<lid>/cancel — отмена со сдвигом хвоста на +7 дней (без тела). */
export function useCancelLesson(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lessonId: number) =>
      api<PlanRow[]>('POST', `/api/admin/groups/${groupId}/plan/${lessonId}/cancel`),
    onSuccess: () => invalidatePlan(qc, groupId),
  });
}
