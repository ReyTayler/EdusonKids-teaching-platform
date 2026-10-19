// Раздел «Задачи» (спека 2026-08-24). Типы 1-в-1 на ответы apps/taskboard.

export type TaskPriority = 'low' | 'normal' | 'high';
export type TaskResolution = 'done' | 'cancelled' | 'irrelevant';
export type StageCategory = 'open' | 'closed';

export interface TaskAssignee {
  id: number;
  full_name: string | null;
  role: string;
}

/**
 * Исполнитель В СТРОКЕ ЗАДАЧИ — только то, чем карточка рисует аватар. Роли
 * здесь нет намеренно: она нужна справочнику (TaskAssignee выше), чтобы
 * подписать человека без имени, а на доске её негде показать.
 */
export interface TaskAssigneeRef {
  id: number;
  full_name: string | null;
}

export interface TaskBoard {
  id: number;
  name: string;
  description: string | null;
  sort_order: number;
  /** Сколько стадий в воронке — счётчик полосы воронок (см. TaskBoardRail). */
  stages_count: number;
  /** Открытые задачи воронки (closed_at IS NULL) — второй счётчик полосы. */
  open_tasks_count: number;
}

export interface TaskStage {
  id: number;
  board_id: number;
  label: string;
  color: string | null;
  category: StageCategory;
  sort_order: number;
}

/** Колонка доски: счётчик без карточек — доска не грузится одним запросом. */
export interface TaskColumnCount {
  stage_id: number;
  label: string;
  color: string | null;
  category: StageCategory;
  sort_order: number;
  count: number;
}

export interface TaskRow {
  id: number;
  board_id: number;
  stage_id: number;
  stage_label: string;
  stage_category: StageCategory;
  stage_color: string | null;
  title: string;
  description: string | null;
  /** Исполнителей может быть несколько; бэкенд отдаёт их отсортированными по имени. */
  assignees: TaskAssigneeRef[];
  created_by_id: number | null;
  created_by_name: string | null;
  student_id: number | null;
  student_name: string | null;
  group_id: number | null;
  group_name: string | null;
  comments_count: number;
  due_date: string | null;
  priority: TaskPriority;
  resolution: TaskResolution | null;
  is_closed: boolean;
  is_overdue: boolean;
  closed_at: string | null;
  stage_entered_at: string;
  updated_at: string;
  created_at: string;
}

export interface TaskActivityItem {
  id: number;
  kind: 'stage_change' | 'assign' | 'comment' | 'system';
  author_id: number | null;
  author_name: string | null;
  text: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export interface TaskFilters {
  board_id?: number;
  stage_id?: number;
  /** «Среди исполнителей есть этот» — фильтр остался одиночным, имя прежнее. */
  assignee_id?: number;
  student_id?: number;
  group_id?: number;
  priority?: TaskPriority;
  only_open?: boolean;
  overdue?: boolean;
  /** Значения селектора «Срок». `overdue` дублирует булев фильтр выше — так на бэке. */
  due?: 'today' | 'week' | 'overdue' | 'none';
  q?: string;
}

/** Собрать query-строку, отбрасывая пустые значения. */
export function taskFilterQS(f: TaskFilters): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === null || v === '' || v === false) continue;
    qs.set(k, String(v));
  }
  return qs.toString();
}

/**
 * Адрес карточки задачи внутри раздела.
 *
 * Карточка живёт в query-параметре, а не в пути, поэтому `EntityLink` (он
 * строит `/admin/<раздел>/<id>`) сюда не годится. Воронку кладём в адрес
 * обязательно: без неё страница откроет первую попавшуюся, и ссылка приведёт
 * получателя не туда.
 */
export function taskPath(boardId: number, taskId: number): string {
  return `/admin/tasks?board=${boardId}&task=${taskId}`;
}
