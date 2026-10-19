import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTaskBoards } from '../../hooks/useTaskStructure';
import { useAuth } from '../../hooks/useAuth';
import { SelectInput } from '../../components/form/SelectInput';
import { SearchInput } from '../../components/ui/SearchInput';
import { EmptyState } from '../../components/ui/EmptyState';
import { Button } from '../../components/ui/Button';
import { PageHeader } from '../../components/shell/PageHeader';
import { TaskBoard } from './TaskBoard';
import { TaskWeekView } from './TaskWeekView';
import { TaskDrawer } from './TaskDrawer';
import { TaskBoardRail, BoardCreateModal } from './TaskBoardRail';
import { TaskSegments } from './TaskSegments';
import { TaskViewSwitcher, type TaskViewMode } from './TaskViewSwitcher';
import { TaskFiltersPopover } from './TaskFiltersPopover';
import { TASK_PRIORITY_LABELS } from '../../lib/labels';
import { canWriteTaskStages, type Role } from '../../lib/permissions';
import type { TaskFilters, TaskPriority } from '../../lib/tasks';

// Ключи фильтр-бара, живущие в URL — общие для доски и недели (спека
// 2026-08-24). `board`/`view`/`task` управляются отдельно выше по файлу.
const FILTER_KEYS = [
  'assignee_id', 'priority', 'only_open', 'overdue', 'q',
  'due', 'stage_id',
];

/**
 * Раздел «Задачи» (спека 2026-08-24): один ряд управления (сегменты, быстрые
 * селекторы, popover «Фильтры», поиск, переключатель вида), под ним рабочая
 * область — сворачиваемая полоса воронок слева и сам вид справа. Воронку
 * выбирали табами в шапке; полоса заменила их (по образцу Weeek).
 */
export default function TasksPage() {
  const [sp, setSp] = useSearchParams();
  const { me } = useAuth();
  const { data: allBoards, isLoading } = useTaskBoards();
  // Диалог первой воронки — только для пустого состояния: когда воронки
  // есть, кнопка «Добавить доску» живёт в полосе слева.
  const [createOpen, setCreateOpen] = useState(false);

  const canManageBoards = canWriteTaskStages(me?.role as Role);

  // Открытая карточка живёт в query-параметре ?task=, а не в локальном state:
  // на неё нужна ссылка со страницы ученика (следующая задача раздела).
  const selectedId = useMemo(() => {
    const raw = sp.get('task');
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }, [sp]);

  const openTask = (taskId: number) => {
    const next = new URLSearchParams(sp);
    next.set('task', String(taskId));
    setSp(next, { replace: true });
  };

  const closeTask = () => {
    const next = new URLSearchParams(sp);
    next.delete('task');
    setSp(next, { replace: true });
  };

  // Архивирование воронок отменили: воронку либо ведут, либо удаляют целиком
  // (удаление — из меню «…» в полосе слева). useMemo остаётся ради стабильной
  // ссылки: список ходит в зависимости эффекта-подстановки ниже.
  const boards = useMemo(() => allBoards || [], [allBoards]);

  const view: TaskViewMode = sp.get('view') === 'week' ? 'week' : 'board';

  const selectedBoardId = useMemo(() => {
    const raw = sp.get('board');
    if (!raw) return null;
    const id = Number(raw);
    return boards.some((b) => b.id === id) ? id : null;
  }, [sp, boards]);

  // Воронка ещё не выбрана в URL (первый заход) или ссылка вела на воронку,
  // которой больше нет (её удалили — хоть из этой же вкладки через меню «…»
  // в полосе) — подставляем первую доступную, чтобы раздел не оставался на
  // пустом экране без всякого объяснения.
  useEffect(() => {
    if (boards.length === 0 || selectedBoardId != null) return;
    const next = new URLSearchParams(sp);
    next.set('board', String(boards[0].id));
    setSp(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boards, selectedBoardId]);

  const setBoard = (value: string) => {
    const next = new URLSearchParams(sp);
    if (value) next.set('board', value); else next.delete('board');
    setSp(next, { replace: true });
  };

  const setView = (v: TaskViewMode) => {
    const next = new URLSearchParams(sp);
    next.set('view', v);
    // На доске фильтра по стадии нет (см. комментарий у `filters` ниже) —
    // оставленный в адресе ключ висел бы невидимым фильтром: поля, которым его
    // снять, на экране уже не будет.
    if (v === 'board') next.delete('stage_id');
    setSp(next, { replace: true });
  };

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(sp);
    if (value) next.set(key, value); else next.delete(key);
    setSp(next, { replace: true });
  };

  // Ключ памоизации — сериализация sp, а не сам объект: useSearchParams
  // отдаёт новый URLSearchParams на каждый рендер даже без смены строки.
  const spKey = sp.toString();

  // Плоский снимок фильтров из URL — сегменты и popover читают его, чтобы
  // подсветить активное состояние, и не заводят своего состояния.
  const filterValues = useMemo(() => Object.fromEntries(
    FILTER_KEYS.map((k) => [k, sp.get(k) ?? '']),
  ), [spKey]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Записать несколько ключей разом: сегменту нужно снять чужие и поставить свой. */
  const applyFilters = (patch: Record<string, string>) => {
    const next = new URLSearchParams(sp);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value); else next.delete(key);
    }
    setSp(next, { replace: true });
  };

  const resetAdvancedFilters = () => {
    applyFilters({ stage_id: '', due: '', priority: '', only_open: '', assignee_id: '' });
  };

  // board_id ОБЯЗАН входить в filters — бэкенд применяет его и к недельному
  // виду (там, в отличие от доски, нет отдельного пути с boardId в URL).
  //
  // stage_id — только в виде «Неделя», и это не забывчивость: вьюха карточек
  // колонки перезаписывает stage_id тем, что пришло в пути URL, поэтому на
  // карточки доски query-параметр не влияет, а счётчики колонок его применяют
  // честно. На доске фильтр по стадии дал бы нули в шапках колонок при живых
  // карточках под ними — счётчик соврал бы относительно списка. В недельном
  // виде такого пути нет, там фильтр работает как задумано.
  const filters: TaskFilters = useMemo(() => ({
    board_id: selectedBoardId ?? undefined,
    assignee_id: sp.get('assignee_id') ? Number(sp.get('assignee_id')) : undefined,
    priority: (sp.get('priority') as TaskPriority) || undefined,
    only_open: sp.get('only_open') === 'true' ? true : undefined,
    overdue: sp.get('overdue') === 'true' ? true : undefined,
    q: sp.get('q') || undefined,
    due: (sp.get('due') as TaskFilters['due']) || undefined,
    stage_id: view === 'week' && sp.get('stage_id') ? Number(sp.get('stage_id')) : undefined,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [spKey, selectedBoardId, view]);

  const priorityOptions = useMemo(() => [
    { value: '', label: 'Любой приоритет' },
    ...Object.entries(TASK_PRIORITY_LABELS).map(([value, label]) => ({ value, label })),
  ], []);

  return (
    <div className="tasks-page">
      {/* Отдельной страницы настроек у раздела больше нет: воронки правятся в
          полосе слева (меню «…» на карточке), стадии — прямо на доске. */}
      <PageHeader title="Задачи" dense />

      {isLoading ? (
        <div className="tasks-page__loading">Загрузка воронок…</div>
      ) : boards.length === 0 ? (
        // Пустое состояние обязано давать выход: раньше отсюда шли на страницу
        // настроек, теперь её нет — заводим первую воронку тем же диалогом,
        // что и полоса слева.
        <EmptyState
          hint={canManageBoards
            ? 'Заведите первую воронку — стадии потом настраиваются прямо на доске.'
            : 'Воронки и стадии задач настраивает суперадмин.'}
          action={canManageBoards ? (
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              + Добавить доску
            </Button>
          ) : undefined}
        >
          Пока нет ни одной воронки задач
        </EmptyState>
      ) : (
        <>
          {/* Один ряд управления на оба вида: слева — чем показываем и что
              показываем, дальше фильтры. Ряд стоит НАД полосой воронок и во всю
              ширину: фильтры действуют на текущий вид целиком, а полоса — это
              навигация по воронкам, ей место вплотную к самой доске. */}
          <div className="tasks-filterbar">
            <TaskViewSwitcher value={view} onChange={setView} />
            <TaskSegments values={filterValues} onApply={applyFilters} />
            <SelectInput
              className="tasks-filterbar__select"
              value={sp.get('priority') ?? ''}
              onChange={(e) => setFilter('priority', e.target.value)}
              options={priorityOptions}
            />
            {selectedBoardId != null && (
              <TaskFiltersPopover
                boardId={selectedBoardId}
                values={filterValues}
                onSet={setFilter}
                onReset={resetAdvancedFilters}
                showStage={view === 'week'}
              />
            )}
            <SearchInput
              value={sp.get('q') ?? ''}
              onChange={(v) => setFilter('q', v)}
              placeholder="Поиск задач: название, #124, ученик…"
              width={280}
            />
          </div>

          <div className="tasks-page__work">
            <TaskBoardRail
              boards={boards}
              selectedId={selectedBoardId}
              onSelect={(id) => setBoard(String(id))}
            />
            <div className="tasks-page__view">
              {view === 'board' ? (
                selectedBoardId != null && (
                  <TaskBoard boardId={selectedBoardId} filters={filters} onOpen={openTask} />
                )
              ) : (
                selectedBoardId != null && (
                  <TaskWeekView filters={filters} onOpen={openTask} />
                )
              )}
            </div>
          </div>
        </>
      )}

      {createOpen && (
        <BoardCreateModal
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => setBoard(String(id))}
        />
      )}

      {selectedId != null && (
        <TaskDrawer id={selectedId} onClose={closeTask} />
      )}

    </div>
  );
}
