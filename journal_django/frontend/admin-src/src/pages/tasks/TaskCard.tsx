import type { KeyboardEvent, ReactNode } from 'react';
import { useDraggable } from '@dnd-kit/core';
import { AvatarStack } from '../../components/ui/AvatarStack';
import { TASK_PRIORITY_LABELS, plural } from '../../lib/labels';
import { fmtDate } from '../../lib/format';
import { isoDate, parseIsoDate, todayMsk } from '../../shared/calendar/lib';
import type { TaskRow } from '../../lib/tasks';

interface ContentProps {
  task: TaskRow;
  /** Недельный вид: срок уже написан на колонке, в карточке он лишний. */
  compact?: boolean;
}

/**
 * Разметка карточки без drag-обвязки — переиспользуется и в самой колонке,
 * и в DragOverlay доски (см. TaskBoard.tsx), как и в разделе «Продления».
 */
export function TaskCardContent({ task, compact }: ContentProps) {
  // Ряд чипов остался ради одного приоритета: тип и метки у задачи убрали
  // (ТЗ 2026-08-28). Ряд по-прежнему собираем списком — соседний meta ниже
  // устроен так же, и добавить в него ещё один чип будет одной строкой.
  const chips = [
    task.priority !== 'normal' && (
      <span key="priority" className={`task-card__chip is-${task.priority}`}>
        {TASK_PRIORITY_LABELS[task.priority]}
      </span>
    ),
  ].filter(Boolean);

  // Нижний ряд собираем так же, как чипы: раньше в нём всегда стоял исполнитель,
  // и пустым он быть не мог. Теперь исполнителей может не быть вовсе — а пустой
  // ряд съел бы отступ карточки ни за что.
  const meta = [
    // Только аватары, без имён (ТЗ 2026-08-26): на карточке шириной в колонку
    // два-три полных имени вытесняют срок и счётчик комментариев.
    task.assignees.length > 0 && (
      <AvatarStack
        key="assignees"
        names={task.assignees.map((a) => a.full_name || '—')}
        size={18}
      />
    ),
    task.comments_count > 0 && (
      <span key="comments" className="task-card__comments" title="Комментарии">
        <CommentGlyph />{task.comments_count}
      </span>
    ),
    !compact && task.due_date && (
      <span
        key="due"
        className={`task-card__due${task.is_overdue ? ' is-overdue' : ''}`}
      >
        {task.is_overdue && <span className="task-card__overdue-dot" aria-hidden="true" />}
        {task.is_overdue ? overdueLabel(task.due_date) : fmtDate(task.due_date)}
      </span>
    ),
  ].filter(Boolean);

  return (
    <>
      <div className="task-card__top">
        <span className="task-card__id">#{task.id}</span>
        {task.student_name && (
          <span className="task-card__student" title={task.student_name}>
            {task.student_name}
          </span>
        )}
      </div>

      {/* Закрытая задача (стадия category='closed') — зачёркнутый заголовок
          с галочкой, а не отдельный статус-бейдж: «закрыто» видно с первого взгляда. */}
      <div className={`task-card__title${task.is_closed ? ' is-closed' : ''}`}>
        {task.is_closed && <CheckGlyph />}
        <span>{task.title}</span>
      </div>

      {/* Показываем только заполненное: пустые свойства ряд не занимают. */}
      {chips.length > 0 && <div className="task-card__chips">{chips}</div>}

      {meta.length > 0 && <div className="task-card__meta">{meta}</div>}
    </>
  );
}

/**
 * «просрочено 2 дн.» вместо голой даты: сама дата ничего не говорит, пока не
 * посчитаешь разницу в уме. Считаем от MSK-сегодня — тем же хелпером, что и
 * календарь, иначе поздним вечером цифра разойдётся с бэкендом.
 */
function overdueLabel(due: string): string {
  const today = parseIsoDate(isoDate(todayMsk()));
  const days = Math.max(1, Math.round(
    (today.getTime() - parseIsoDate(due).getTime()) / 86_400_000,
  ));
  return `просрочено ${days} ${plural(days, 'день', 'дня', 'дней')}`;
}

interface Props {
  task: TaskRow;
  stageId: number;
  onOpen: (id: number) => void;
  /** Недельный вид: срок в карточке не дублируем (см. ContentProps). */
  compact?: boolean;
  /** Быстрые действия — показываются только на наведении/фокусе. */
  actions?: ReactNode;
}

export function TaskCard({ task, stageId, onOpen, compact, actions }: Props) {
  // Данные карточки едут вместе с drag'ом (event.active.data) — доска читает их
  // прямо из события, не из кэша колонки (тот же приём, что и в RenewalCardView).
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: task.id,
    data: { task, fromStageId: stageId },
  });

  // dnd-kit ставит role="button"/tabIndex сам (карточка перетаскиваема), но
  // клавиатуру этим не даёт — Enter/пробел молчат без явного обработчика.
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    onOpen(task.id);
  };

  return (
    <div
      ref={setNodeRef}
      className={`task-card${isDragging ? ' task-card--dragging' : ''}`}
      onClick={() => onOpen(task.id)}
      onKeyDown={handleKeyDown}
      {...listeners}
      {...attributes}
    >
      <TaskCardContent task={task} compact={compact} />
      {actions && (
        <div
          className="task-card__actions"
          // Клик по действию не должен открывать панель и стартовать drag.
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {actions}
        </div>
      )}
    </div>
  );
}

function CheckGlyph() {
  return (
    <svg
      className="task-card__check"
      width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function CommentGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}
