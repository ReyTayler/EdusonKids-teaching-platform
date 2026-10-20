// journal_django/frontend/admin-src/src/lib/sync.ts
// Загрузчики из Google-таблиц школы и пересборки по данным БД (зарплата, счётчики,
// план, доп.уроки, продления) в Eduson удалены: первые искали ученика по имени (у
// Eduson тёзки), вторые стирали офсеты, расписание и стадии, перенесённые из старой
// системы. Осталась проверка плана; данные старой системы — мягкое обновление с прода.
export type SyncAction = 'check-plan-health';

export interface SyncActionDef {
  action: SyncAction;
  label: string;
  /** Действие только читает: чекбокс «только предпросмотр» не показываем. */
  readOnly?: boolean;
}

export const SYNC_ACTIONS: SyncActionDef[] = [
  {
    action: 'check-plan-health',
    label: 'Планы групп — проверка (ничего не меняет: покажет, где план разъехался с занятиями)',
    readOnly: true,
  },
];

export type SyncTaskState = 'PENDING' | 'STARTED' | 'SUCCESS' | 'FAILURE';

export interface SyncStatus {
  state: SyncTaskState;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface SyncRunResponse {
  task_id: string;
}

/** Мягкое обновление с прода (GET/POST /api/admin/sync/prod-refresh). */
export interface ProdRefreshStatus {
  /** Кнопка включена флагом EDUSON_PROD_REFRESH_BUTTON (только локально). */
  enabled: boolean;
  running: boolean;
  started_at: string | null;
  /** Итог последнего запуска по логу; null — идёт или запусков не было. */
  last: { at: string; ok: boolean; text: string } | null;
  /** Строки лога последнего запуска (без ПДн: id Eduson, коды групп, числа). */
  log_tail: string[];
}
