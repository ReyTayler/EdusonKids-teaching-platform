import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { SelectInput } from '../../components/form/SelectInput';
import { DateInput } from '../../components/form/DateInput';
import { Combobox } from '../../components/form/Combobox';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { useTaskList, useTaskMutations } from '../../hooks/useTasks';
import { useTaskAssignees, useTaskBoards } from '../../hooks/useTaskStructure';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { conflictError } from '../tasks/TaskBoard';
import { fmtDate } from '../../lib/format';
import { ROLE_LABELS } from '../../lib/labels';
import type { Role } from '../../lib/permissions';
import { taskPath } from '../../lib/tasks';
import type { TaskAssignee, TaskRow } from '../../lib/tasks';

function assigneeLabel(a: TaskAssignee): string {
  return a.full_name || ROLE_LABELS[a.role as Role] || a.role;
}

/** Строка задачи: номер, заголовок, стадия, исполнитель, срок. Клик ведёт
 *  в раздел «Задачи» с открытой панелью карточки (та же ссылка, что и
 *  «следующая задача» в TasksPage). */
function TaskRowLine({ task }: { task: TaskRow }) {
  return (
    <Link
      to={taskPath(task.board_id, task.id)}
      className={`stasks__row${task.is_closed ? ' is-closed' : ''}`}
    >
      <span className="stasks__id">#{task.id}</span>
      <span className="stasks__title">{task.title}</span>
      <span
        className="stasks__stage"
        style={{ '--stage-c': task.stage_color || 'var(--text3)' } as CSSProperties}
      >
        {task.stage_label}
      </span>
      {/* Исполнителей может быть несколько — перечисляем их текстом: строка
          списка узкая, аватары в ней встали бы вровень с подписями соседних
          колонок и читались бы как ещё одно свойство. */}
      <span className="stasks__assignee">
        {task.assignees.length > 0
          ? task.assignees.map((a) => a.full_name || '—').join(', ')
          : 'Не назначен'}
      </span>
      {task.due_date && (
        <span className={`stasks__due${task.is_overdue ? ' is-overdue' : ''}`}>
          {fmtDate(task.due_date)}
        </span>
      )}
    </Link>
  );
}

interface CreateDialogProps {
  studentId: number;
  onClose: () => void;
}

/** Диалог быстрой постановки задачи ученику — воронка, заголовок, срок,
 *  исполнитель. student_id подставлен и не редактируется. */
function CreateTaskDialog({ studentId, onClose }: CreateDialogProps) {
  const { data: allBoards } = useTaskBoards();
  const { data: assignees } = useTaskAssignees();
  const { create } = useTaskMutations();
  const showError = useApiError();
  const { toast } = useToast();

  // Архивирования воронок больше нет — предлагаем все, что вернул сервер.
  // useMemo здесь только ради стабильной ссылки: без него эффект ниже
  // (подстановка первой воронки) пересчитывался бы на каждый рендер.
  const boards = useMemo(() => allBoards || [], [allBoards]);

  const [boardId, setBoardId] = useState<string>('');
  const [title, setTitle] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [assigneeId, setAssigneeId] = useState('');

  // Воронка ещё не выбрана (диалог только открылся) — подставляем первую
  // доступную, чтобы форму можно было отправить одним заголовком.
  useEffect(() => {
    if (!boardId && boards.length > 0) setBoardId(String(boards[0].id));
  }, [boards, boardId]);

  const assigneeOptions = [
    { value: '', label: '— не назначен —' },
    ...(assignees || []).map((a) => ({ value: String(a.id), label: assigneeLabel(a) })),
  ];

  const canSubmit = Boolean(boardId) && title.trim().length > 0 && !create.isPending;

  const handleSubmit = () => {
    const trimmed = title.trim();
    if (!boardId || !trimmed) return;
    create.mutate(
      {
        board_id: Number(boardId),
        title: trimmed,
        student_id: studentId,
        due_date: dueDate || null,
        // Список, а не одиночный id: бэкенд принимает только assignee_ids, и
        // прежний ключ он молча игнорировал бы — задача создавалась бы ничьей.
        // Здесь ставим одного: быструю задачу заводят на конкретного человека,
        // а добрать соисполнителей можно в панели.
        assignee_ids: assigneeId ? [Number(assigneeId)] : [],
      },
      {
        onSuccess: () => {
          toast('Задача создана', 'ok');
          onClose();
        },
        onError: (err) => showError(conflictError(err), 'Не удалось создать задачу'),
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Поставить задачу"
      footer={
        <button
          type="submit"
          form="student-task-create-form"
          className="btn-add"
          disabled={!canSubmit}
        >
          Создать
        </button>
      }
    >
      <form
        id="student-task-create-form"
        className="modal-form"
        onSubmit={(e) => { e.preventDefault(); handleSubmit(); }}
      >
        <Field label="Воронка" required full>
          <SelectInput
            value={boardId}
            onChange={(e) => setBoardId(e.target.value)}
            options={boards.map((b) => ({ value: b.id, label: b.name }))}
            placeholder={boards.length === 0 ? 'Нет доступных воронок' : 'Выберите воронку'}
            disabled={boards.length === 0}
          />
        </Field>
        <Field label="Заголовок" required full>
          <TextInput
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Что нужно сделать"
            autoFocus
          />
        </Field>
        <Field label="Срок">
          <DateInput value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
        </Field>
        <Field label="Исполнитель">
          <Combobox
            value={assigneeId}
            onChange={setAssigneeId}
            options={assigneeOptions}
            placeholder="— не назначен —"
            aria-label="Исполнитель"
          />
        </Field>
      </form>
    </Dialog>
  );
}

interface Props {
  studentId: number;
}

/**
 * Задачи ученика на его карточке (спека 2026-08-24/26, задача 9). Открытые
 * задачи видны сразу, закрытые — под свёрткой (тот же приём, что архив групп
 * преподавателя в TeacherGroupsBlock).
 *
 * Закрытые задачи получены вторым запросом БЕЗ only_open и отфильтрованы на
 * месте (is_closed) — так проще, чем городить второй параметр фильтра или
 * держать один список с ручной сортировкой по стадиям; цена — секция
 * закрытых опирается на первую страницу (до 50 записей), что с большим
 * запасом покрывает объём задач одного ученика.
 */
export default function StudentTasksBlock({ studentId }: Props) {
  const [creating, setCreating] = useState(false);
  const [closedOpen, setClosedOpen] = useState(false);

  const openQuery = useTaskList({ student_id: studentId, only_open: true });
  const allQuery = useTaskList({ student_id: studentId });

  const openRows = openQuery.data?.rows ?? [];
  const closedRows = (allQuery.data?.rows ?? []).filter((t) => t.is_closed);

  const isLoading = openQuery.isLoading;

  return (
    <div className="stasks">
      <div className="stasks__head">
        <h3 className="sub-header">
          Задачи {!isLoading && <span className="count-badge">{openRows.length}</span>}
        </h3>
        <Button variant="secondary" size="sm" onClick={() => setCreating(true)}>
          + Поставить задачу
        </Button>
      </div>

      {isLoading ? (
        <div className="stasks__loading">Загружаем задачи…</div>
      ) : openRows.length === 0 && closedRows.length === 0 ? (
        <EmptyState hint="Нажмите «Поставить задачу», чтобы завести первую.">
          По ученику пока нет задач
        </EmptyState>
      ) : openRows.length === 0 ? (
        <EmptyState hint="Все задачи по ученику закрыты — список ниже.">
          Открытых задач нет
        </EmptyState>
      ) : (
        <div className="stasks__list">
          {openRows.map((t) => <TaskRowLine key={t.id} task={t} />)}
        </div>
      )}

      {closedRows.length > 0 && (
        <>
          <button
            type="button"
            className="stasks__toggle"
            onClick={() => setClosedOpen((v) => !v)}
            aria-expanded={closedOpen}
          >
            <svg
              width="12" height="12" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"
              strokeLinejoin="round" aria-hidden="true"
              className={closedOpen ? 'is-open' : ''}
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
            Закрытые <span className="count-badge">{closedRows.length}</span>
          </button>
          {closedOpen && (
            <div className="stasks__list">
              {closedRows.map((t) => <TaskRowLine key={t.id} task={t} />)}
            </div>
          )}
        </>
      )}

      {creating && <CreateTaskDialog studentId={studentId} onClose={() => setCreating(false)} />}
    </div>
  );
}
