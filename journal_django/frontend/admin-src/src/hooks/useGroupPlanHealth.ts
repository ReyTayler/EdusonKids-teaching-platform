/**
 * «Починка плана группы» — GET /plan/health (предпросмотр) + POST /plan/resync
 * (применение). Обе точки — IsSuperAdmin (apps/scheduling/views.py), поэтому
 * страница обязана держать enabled на canFixPlan сама (GroupPlanHealthBlock) —
 * иначе менеджер ловит 403 при каждом открытии вкладки «Расписание».
 *
 * Формы ответов — apps/scheduling/health.py::check_group и
 * apps/scheduling/repository.py::plan_resync_diff/resync_plan_facts (см. там
 * же комментарии о разнице id-семантики между проверками).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { groupPlanKey } from './useGroupPlanCalendar';
import type { PlanRow } from './useGroupPlanCalendar';

/** Строка findings[key]. ВНИМАНИЕ: у 'fact_without_position' и 'duplicate_dates'
 * поле id — id ЗАНЯТИЯ (у него нет своей позиции в плане), у остальных пяти
 * проверок — id плановой строки. seq/scheduled_date у занятий пустые. */
export interface PlanHealthFinding {
  id: number;
  seq: number | null;
  lesson_number: number | null;
  scheduled_date: string | null;
  fact_date: string | null;
}

export interface PlanResyncChangeSide {
  fact_lesson_id: number | null;
  scheduled_date: string | null;
}

export interface PlanResyncChange {
  position_id: number;
  lesson_number: number | null;
  from: PlanResyncChangeSide;
  to: PlanResyncChangeSide;
}

export interface PlanResyncFreedPosition {
  position_id: number;
  lesson_number: number | null;
  scheduled_date: string | null;
}

/** reason: 'no_position' | 'locked_position' | 'duplicate_fact_number'
 * (apps/scheduling/repository.py::_plan_resync_changes) — строка, а не union:
 * новую причину бэк может добавить без синхронной правки фронта, подпись
 * просто провалится в fallback (см. PLAN_HEALTH_ORPHAN_REASON_LABELS). */
export interface PlanResyncOrphanFact {
  lesson_id: number;
  lesson_number: number | null;
  lesson_date: string;
  reason: string;
}

export interface PlanHealthResync {
  /** Непусто → чинить нельзя. Источник истины для UI — это поле, а не
   * собственный пересчёт findings на клиенте. */
  blocked_by: string[];
  /** null, когда blocked_by непусто (сервер намеренно не показывает план
   * починки, который сам же откажется применять). */
  changes: PlanResyncChange[] | null;
  orphan_facts: PlanResyncOrphanFact[];
  freed: PlanResyncFreedPosition[];
}

export interface PlanHealthReport {
  group_id: number;
  name: string;
  findings: Record<string, PlanHealthFinding[]>;
  resync: PlanHealthResync;
}

/** [position_id, fact_lesson_id|null, 'YYYY-MM-DD'] — ровно то, что ждёт
 * PlanResyncSerializer.validate_expected. */
export type ResyncExpectedTriple = [number, number | null, string];

export interface PlanResyncResult {
  applied: number;
  /** Число освобождённых позиций. В /plan/health под ключом freed лежит СПИСОК
   * позиций — поэтому здесь имя другое, чтобы формы не перепутались. */
  freed_count: number;
  plan: PlanRow[];
}

export const groupPlanHealthKey = (groupId: number) => ['group-plan-health', groupId] as const;

/** GET /plan/health. enabled должен быть завязан на canFixPlan вызывающей
 * стороной — см. предупреждение выше про 403 на каждое открытие вкладки. */
export function useGroupPlanHealth(groupId: number, enabled: boolean) {
  return useQuery({
    queryKey: groupPlanHealthKey(groupId),
    queryFn: () => api<PlanHealthReport>('GET', `/api/admin/groups/${groupId}/plan/health`),
    enabled: enabled && Number.isFinite(groupId) && groupId > 0,
  });
}

/** POST /plan/resync. Тело — [[position_id, fact_lesson_id|null, date], ...],
 * собранное из resync.changes предпросмотра (рукопожатие с сервером: при
 * расхождении — 409 «Состояние изменилось»). Инвалидация зеркалит
 * useGroupPlan.ts: план, health-предпросмотр и список групп (слоты/статусы
 * плана видны и там). Инвалидируем health и на 409 тоже — предпросмотр обязан
 * обновиться сразу, а не только по следующему открытию вкладки. */
export function useResyncPlan(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (expected: ResyncExpectedTriple[]) =>
      api<PlanResyncResult>('POST', `/api/admin/groups/${groupId}/plan/resync`, { expected }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupPlanKey(groupId) });
      qc.invalidateQueries({ queryKey: groupPlanHealthKey(groupId) });
      qc.invalidateQueries({ queryKey: ['groups'] });
    },
    onError: () => {
      qc.invalidateQueries({ queryKey: groupPlanHealthKey(groupId) });
    },
  });
}
