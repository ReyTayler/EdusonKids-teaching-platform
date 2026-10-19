import { useState, type KeyboardEvent } from 'react';
import {
  DndContext, DragOverlay, PointerSensor, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent,
} from '@dnd-kit/core';
import { useStageMutations, useTaskColumns, useTaskStages } from '../../hooks/useTaskStructure';
import { useTaskMutations } from '../../hooks/useTasks';
import { useApiError } from '../../hooks/useApiError';
import { useAuth } from '../../hooks/useAuth';
import { ApiError } from '../../lib/api';
import { TASK_CONFLICT_LABELS } from '../../lib/labels';
import { canWriteTaskStages, type Role } from '../../lib/permissions';
import { TaskColumn } from './TaskColumn';
import { TaskCardContent } from './TaskCard';
import { TaskCompleteDialog } from './TaskCompleteDialog';
import { EmptyState } from '../../components/ui/EmptyState';
import { Button } from '../../components/ui/Button';
import { TextInput } from '../../components/form/TextInput';
import { useToast } from '../../components/ui/Toast';
import type { TaskFilters, TaskResolution, TaskRow } from '../../lib/tasks';

interface Props {
  boardId: number;
  filters: TaskFilters;
  onOpen: (id: number) => void;
}

/**
 * Карточка, которой правят результат в TaskCompleteDialog. Перетаскивание в
 * закрытую стадию закрывает задачу сразу с 'done'; диалог открывается уже
 * постфактум — действием «Изменить результат» из toast'а.
 */
interface CompleteTarget {
  taskId: number;
  toStageId: number;
}

/** Перенос карточки, который toast предлагает отменить. */
interface MoveRequest {
  taskId: number;
  fromStageId: number;
  toStageId: number;
  /** Результат для целевой стадии — обязателен, если она закрытая. */
  resolution?: TaskResolution;
  /**
   * Результат для отката: если карточку тащили ИЗ закрытой стадии, возврат в
   * неё бэкенд без результата тоже не пустит (400). Берём тот, что был у задачи
   * до переноса — «Отменить» обязано вернуть ровно прежнее состояние.
   */
  undoResolution?: TaskResolution;
  stageLabel: string;
}

// Код конфликта из ApiError.message превращаем в человеческий текст —
// тот же приём, что RenewalStagesSettings применяет для DELETE_STAGE_ERRORS.
// Экспортирован — TaskDrawer использует тот же приём для своих мутаций.
export function conflictError(err: unknown): unknown {
  if (err instanceof ApiError && err.message && TASK_CONFLICT_LABELS[err.message]) {
    return new Error(TASK_CONFLICT_LABELS[err.message]);
  }
  return err;
}

export function TaskBoard({ boardId, filters, onOpen }: Props) {
  const { data: columns, isLoading, isError, refetch } = useTaskColumns(boardId);
  const { data: stages } = useTaskStages(boardId);
  const { move } = useTaskMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const { me } = useAuth();

  // Карточка, которую сейчас тащат — рендерится отдельно в DragOverlay (портал
  // в document.body), чтобы не обрезаться overflow колонки (тот же приём, что
  // и в RenewalBoard).
  const [activeTask, setActiveTask] = useState<TaskRow | null>(null);
  const [completeTarget, setCompleteTarget] = useState<CompleteTarget | null>(null);

  // Небольшой порог перед стартом драга — иначе клик по карточке (открытие
  // панели справа) будет перехватываться сенсором как начало drag'а.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const dragTask = (event: DragStartEvent | DragEndEvent): TaskRow | undefined =>
    (event.active.data.current as { task?: TaskRow } | undefined)?.task;
  const dragFromStage = (event: DragEndEvent): number | undefined =>
    (event.active.data.current as { fromStageId?: number } | undefined)?.fromStageId;

  const handleDragStart = (event: DragStartEvent) => {
    setActiveTask(dragTask(event) ?? null);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveTask(null);
    const { active, over } = event;
    if (!over) return;

    const taskId = Number(active.id);
    const toStageId = Number(over.id);
    const fromStageId = dragFromStage(event);
    if (fromStageId == null || fromStageId === toStageId) return; // тот же столбец — не трогаем

    const targetStage = (stages || []).find((s) => s.id === toStageId);
    if (!targetStage) return;

    // Тащили ИЗ закрытой стадии — откат вернёт задачу в неё, а такой перенос
    // требует результата. Берём прежний результат карточки (fallback 'done' —
    // на исторические записи, закрытые до появления обязательного поля).
    const fromStage = (stages || []).find((s) => s.id === fromStageId);
    const undoResolution = fromStage?.category === 'closed'
      ? (dragTask(event)?.resolution ?? 'done')
      : undefined;

    // Перенос в закрытую стадию закрывает задачу сразу с результатом «Выполнено»:
    // сценарий доски — «перетащил, и всё». Результат правится из toast'а, а
    // диалог остаётся на кнопке «Выполнено» в панели задачи.
    moveWithUndo({
      taskId,
      fromStageId,
      toStageId,
      resolution: targetStage.category === 'closed' ? 'done' : undefined,
      undoResolution,
      stageLabel: targetStage.label,
    });
  };

  const moveWithUndo = (req: MoveRequest) => {
    move.mutate(
      { id: req.taskId, to_stage_id: req.toStageId, resolution: req.resolution },
      {
        onSuccess: () => {
          const actions = [{
            label: 'Отменить',
            // Возврат в открытую стадию бэкенд отработает сам: move_task
            // обнуляет resolution и closed_at, отдельного «переоткрыть» не нужно.
            onClick: () => move.mutate(
              { id: req.taskId, to_stage_id: req.fromStageId, resolution: req.undoResolution },
              { onError: (err) => showError(conflictError(err), 'Не удалось отменить перенос') },
            ),
          }];
          if (req.resolution) {
            actions.push({
              label: 'Изменить результат',
              onClick: () => setCompleteTarget({ taskId: req.taskId, toStageId: req.toStageId }),
            });
          }
          toast(`Задача перемещена в «${req.stageLabel}»`, { kind: 'ok', actions });
        },
        onError: (err) => showError(conflictError(err), 'Не удалось перенести задачу'),
      },
    );
  };

  const handleCompleteConfirm = (resolution: TaskResolution) => {
    if (!completeTarget) return;
    move.mutate(
      { id: completeTarget.taskId, to_stage_id: completeTarget.toStageId, resolution },
      {
        onSuccess: () => setCompleteTarget(null),
        onError: (err) => {
          setCompleteTarget(null);
          showError(conflictError(err), 'Не удалось изменить результат');
        },
      },
    );
  };

  if (isLoading) {
    return <div className="task-board__loading">Загружаем доску…</div>;
  }

  if (isError && !columns) {
    return (
      <EmptyState
        hint="Проверьте соединение и попробуйте ещё раз"
        action={<Button variant="secondary" onClick={() => refetch()}>Повторить</Button>}
      >
        Не удалось загрузить доску задач
      </EmptyState>
    );
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <div className="task-board">
        {(columns || []).map((col) => (
          <TaskColumn key={col.stage_id} col={col} boardId={boardId} filters={filters} onOpen={onOpen} />
        ))}
        {/* Плитку видит только тот, кому бэкенд разрешит POST стадии
            (ReadStaffWriteAdmin) — иначе нажатие кончалось бы 403. */}
        {canWriteTaskStages(me?.role as Role) && <AddStageTile boardId={boardId} />}
      </div>
      <DragOverlay>
        {activeTask && (
          <div className="task-card task-card--overlay">
            <TaskCardContent task={activeTask} />
          </div>
        )}
      </DragOverlay>
      {completeTarget && (
        <TaskCompleteDialog
          open
          pending={move.isPending}
          onClose={() => setCompleteTarget(null)}
          onConfirm={handleCompleteConfirm}
        />
      )}
    </DndContext>
  );
}

/**
 * Плитка «Добавить колонку» в конце ленты: стадию заводят прямо на доске —
 * отдельной страницы настроек у раздела больше нет. По клику разворачивается
 * в поле названия — тот же приём, что у «+ Добавить задачу» внизу колонки.
 *
 * Категория новой стадии — всегда 'open': закрытую колонку заводят редко, а
 * создать задачу в ней бэкенд всё равно не даст. Вид меняется в «Настройках»
 * стадии. Цвет не передаём — тон выведется из названия (stageTone).
 */
function AddStageTile({ boardId }: { boardId: number }) {
  const { create } = useStageMutations(boardId);
  const showError = useApiError();
  const { toast } = useToast();
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState('');

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setLabel('');
      setAdding(false);
      return;
    }
    if (e.key !== 'Enter') return;
    const trimmed = label.trim();
    if (!trimmed) return;
    create.mutate(
      { label: trimmed, category: 'open' },
      {
        // В отличие от карточек, колонки заводят по одной — поле сворачиваем.
        onSuccess: () => { setLabel(''); setAdding(false); toast(`Стадия «${trimmed}» создана`, 'ok'); },
        onError: (err) => showError(conflictError(err), 'Не удалось создать стадию'),
      },
    );
  };

  return (
    <div className="task-col task-col--add">
      {adding ? (
        <TextInput
          className="task-col__add-stage-input"
          value={label}
          autoFocus
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={handleKey}
          onBlur={() => { if (!label.trim()) setAdding(false); }}
          placeholder="Название колонки"
          disabled={create.isPending}
          aria-label="Название новой стадии"
        />
      ) : (
        <button
          type="button"
          className="task-col__add-stage"
          onClick={() => setAdding(true)}
        >
          + Добавить колонку
        </button>
      )}
    </div>
  );
}
