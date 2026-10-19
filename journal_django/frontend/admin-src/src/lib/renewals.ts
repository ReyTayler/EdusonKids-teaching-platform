// Типы для раздела «Продления» (CRM-воронка продлений учеников).
// Бэкенд: /api/admin/renewals* (Django+DRF, см. журнал изменений плана
// docs/superpowers/plans/2026-07-08-renewals-crm-pipeline.md).

/** Порог SLA: дольше этого числа дней в стадии → сделка «застряла» (красный
 *  бейдж). Единый источник для канбана (RenewalCardView) и списка (RenewalList). */
export const SLA_OVERDUE_DAYS = 5;

export type StageKind = 'progress' | 'decision' | 'won' | 'lost';

/**
 * Ключ стадии «Заморожен» — зеркалит `apps/renewals/transitions.py::FROZEN_KEY`.
 * Особые ПРАВИЛА ПЕРЕХОДА больше не привязаны к ключу — они у любой стадии с
 * `allow_mid_cycle` (см. isPauseStage). Ключ остался нужен только там, где у
 * заморозки особая ОБВЯЗКА: она одна спрашивает срок («до какого месяца»).
 */
export const FROZEN_STAGE_KEY = 'frozen';

/**
 * Стадия-«пауза»: вход разрешён с любой стадии и посреди незавершённого цикла,
 * выход — только действием «Вернуть в работу». Зеркалит
 * `apps/renewals/transitions.py::_is_pause_target`, включая требование
 * kind='decision': на стадии другого вида флаг ничего не даёт, и UI не должен
 * предлагать переход, который бэк отклонит.
 */
export function isPauseStage(stage: Pick<RenewalStage, 'kind' | 'allow_mid_cycle'>): boolean {
  return stage.allow_mid_cycle && stage.kind === 'decision';
}

export type StageTone = 'info' | 'muted' | 'positive' | 'negative';

/**
 * Тон бейджа стадии — ЕДИНЫЙ для всех мест, где стадия показывается: список
 * сделок, канбан, панель сделки и бейдж ученика (стадия последней сделки =
 * «статус» ученика, спека 2026-07-25). Иначе одна и та же «Думает» оказывалась
 * серой в одном экране и синей в другом.
 *
 * Цвет стадии из БД (stage.color) здесь не участвует: он принадлежит колонкам
 * доски, а в таблицах и героях цвет несёт семантику из токенов. Промежуточные
 * решения (в том числе «Заморожен») намеренно нейтральны — это не оценка.
 */
const STAGE_TONE: Record<StageKind, StageTone> = {
  progress: 'info',
  decision: 'muted',
  won: 'positive',
  lost: 'negative',
};

export function stageTone(kind: StageKind): StageTone {
  return STAGE_TONE[kind] ?? 'muted';
}

export interface RenewalStage {
  id: number;
  key: string;
  label: string;
  color: string | null;
  kind: StageKind;
  is_auto: boolean;
  /** «Пауза, а не решение» — см. isPauseStage. */
  allow_mid_cycle: boolean;
  sort_order: number;
}

/** Активное направление ученика — справочная информация на карточке. */
export interface RenewalDirection {
  name: string;
  color: string | null;
}

export interface RenewalCard {
  id: number;
  student_id: number;
  student_name: string;
  /** Активные направления ученика (сделка — per ученик, подписочная модель). */
  directions: RenewalDirection[];
  cycle_no: number;
  /** Дата отработки 4-го урока цикла (созревание продления). */
  due_at: string | null;
  assignee_name: string | null;
  days_in_stage: number;
  /** Баланс ученика В УРОКАХ (оплачено минус посещено). Отрицательный = долг. */
  balance: number;
  /** Баланс < 0. Дублирует знак `balance`; оставлен для читаемости условий. */
  debt: boolean;
  /** Отработаны ли все 4 урока текущего цикла — пока false, «Продлён» недоступен. */
  cycle_completed: boolean;
  /** «До какого месяца заморозка» ('YYYY-MM-01'); непусто только на стадии frozen. */
  frozen_until_month: string | null;
}

export interface RenewalColumn {
  stage_id: number;
  key: string;
  label: string;
  kind: StageKind;
  color: string | null;
  count: number;
  cards: RenewalCard[];
}

export interface RenewalBoard {
  columns: RenewalColumn[];
}

export interface RenewalListRow {
  id: number;
  student_name: string;
  directions: RenewalDirection[];
  cycle_no: number;
  stage_label: string;
  stage_kind: StageKind;
  stage_color: string | null;
  due_at: string | null;
  assignee_name: string | null;
  days_in_stage: number;
  /** «До какого месяца заморозка» ('YYYY-MM-01'); непусто только на стадии frozen. */
  frozen_until_month: string | null;
}

export interface RenewalDealDetail {
  id: number;
  student_id: number;
  cycle_no: number;
  stage_id: number;
  stage_key: string;
  stage_label: string;
  stage_kind: StageKind;
  stage_color: string | null;
  assignee_id: number | null;
  assignee_name: string | null;
  reason_code: string | null;
  /** Дата отработки 4-го урока цикла (созревание продления). */
  due_at: string | null;
  stage_entered_at: string;
  outcome_at: string | null;
  created_at: string;
  student_name: string;
  directions: RenewalDirection[];
  days_in_stage: number;
  lesson_in_cycle: number;
  /** true — все 4 урока цикла отработаны, пора продлевать. */
  cycle_completed: boolean;
  balance: number;
  /** Баланс ученика < 0 — красный бейдж «Долг». */
  debt: boolean;
  /** «До какого месяца заморозка» ('YYYY-MM-01'); непусто только на стадии frozen. */
  frozen_until_month: string | null;
  /**
   * Можно ли вернуть сделку в работу. Переоткрывать разрешено только последнюю
   * сделку ученика: оживление старого цикла поверх более позднего закрытого
   * рвало нумерацию и заводило сделку впереди посещаемости (прод, 25.08.2026).
   * Считает бэк тем же правилом, что и гард — кнопку по нему прячем, а не
   * показываем ведущей в 409.
   */
  can_reopen: boolean;
}

export interface RenewalActivityItem {
  id: number;
  kind: 'stage_change' | 'comment' | 'payment_linked' | 'system';
  body: string | null;
  created_at: string;
  from_label: string | null;
  to_label: string | null;
  author_name: string | null;
  payment_id: number | null;
}

export interface RenewalFilters {
  assignee_id?: string;
  direction_id?: string;
  stage_id?: string;
  /** Поиск по имени ученика: в канбане — per-column, в списке — общий фильтр. ILIKE. */
  student?: string;
  /** Номер цикла (точное совпадение) — только списочный вид. */
  cycle_no?: string;
  /** 'true' — списочный вид включает закрытые (won/lost) сделки. */
  include_closed?: string;
}

/** Кандидат в ответственные по сделке (менеджер/админ). */
export interface RenewalAssignee {
  id: number;
  full_name: string;
}

export type RenewalLostReason = 'price' | 'schedule' | 'lost_interest' | 'relocation' | 'other';

/** Строка сводки «Ученики без сделок» (активный membership, сделок не было ни разу). */
export interface RenewalUnassignedRow {
  student_id: number;
  student_name: string;
  directions: RenewalDirection[];
  /** Суммарно посещено уроков за всю историю. */
  attended: number;
  /** Расчётный номер цикла будущей сделки. */
  cycle_no: number;
  debt: boolean;
}
