import {
  useCallback, useEffect, useMemo, useState, type ReactNode,
} from 'react';
import { Avatar } from '../../components/Avatar';
import { EntityLink } from '../../components/EntityLink';
import { SelectInput } from '../../components/form/SelectInput';
import { DateInput } from '../../components/form/DateInput';
import { TextInput } from '../../components/form/TextInput';
import { Textarea } from '../../components/form/Textarea';
import { Combobox } from '../../components/form/Combobox';
import { MultiSelect } from '../../components/form/MultiSelect';
import { AvatarStack } from '../../components/ui/AvatarStack';
import { Button } from '../../components/ui/Button';
import { ActionMenu, type ActionMenuItem } from '../../components/ui/ActionMenu';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { DrawerResizeHandle } from '../../components/ui/DrawerResizeHandle';
import {
  ActivityEmpty, ActivityLog, ActivityRow, CollapsibleSection, CommentThread,
} from '../../components/ui/DrawerFeed';
import { useToast } from '../../components/ui/Toast';
import {
  useTask, useTaskActivity, useTaskMutations,
} from '../../hooks/useTasks';
import { useTaskAssignees, useTaskStages } from '../../hooks/useTaskStructure';
import { useStudentsAll } from '../../hooks/useStudents';
import { useGroupsAll } from '../../hooks/useGroups';
import { useApiError } from '../../hooks/useApiError';
import { useAuth } from '../../hooks/useAuth';
import { useDrawerResize } from '../../hooks/useDrawerResize';
import { fmtDate, fmtDateTime, fmtRelativeDateTime, studentOptionLabel } from '../../lib/format';
import {
  ROLE_LABELS, TASK_FIELD_PHRASES, TASK_PRIORITY_LABELS, TASK_RESOLUTION_LABELS,
} from '../../lib/labels';
import { canDeleteTask, type Role } from '../../lib/permissions';
import { stageTone } from '../../lib/stage-tone';
import type {
  TaskActivityItem, TaskAssignee, TaskResolution, TaskStage,
} from '../../lib/tasks';
import { taskPath } from '../../lib/tasks';
import { conflictError } from './TaskBoard';
import { InlineField } from '../../components/form/InlineField';
import {
  CalendarGlyph, GroupGlyph, PersonGlyph, PriorityGlyph, StudentGlyph,
} from '../../components/form/FieldIcons';
import { TaskCompleteDialog } from './TaskCompleteDialog';

interface Props {
  id: number;
  onClose: () => void;
}

/** Подпись исполнителя: full_name, а если он пуст — роль (спека раздела). */
function assigneeLabel(a: TaskAssignee): string {
  return a.full_name || ROLE_LABELS[a.role as Role] || a.role;
}

function stageLabelOf(stages: TaskStage[] | undefined, id: unknown): string {
  if (id == null) return '—';
  const found = (stages || []).find((s) => s.id === Number(id));
  return found?.label ?? '—';
}

function assigneeLabelOf(assignees: TaskAssignee[] | undefined, id: unknown): string {
  if (id == null) return '— не назначен —';
  const found = (assignees || []).find((a) => a.id === Number(id));
  if (!found) return `#${id}`;
  return assigneeLabel(found);
}

const TASK_DRAWER_WIDTH_KEY = 'taskboard.drawerWidth';

/**
 * Справочники для расшифровки сырых id из ленты истории. Собираются в
 * TaskDrawer из УЖЕ загруженных им списков и прокидываются вниз — своих
 * запросов лента не делает.
 */
interface ActivityRefs {
  students: { id: number; full_name: string; parent1_name?: string | null }[] | undefined;
  groups: { id: number; name: string }[] | undefined;
}

/**
 * Значение поля из meta системной записи — в читаемый вид.
 *
 * Бэкенд кладёт в meta сырьё (id связанных сущностей, даты в ISO) намеренно:
 * текст, собранный в момент правки, переврал бы историю после переименования
 * ученика или группы. Поэтому подписи выводим здесь, по текущим справочникам.
 */
function formatFieldValue(field: string, value: unknown, refs: ActivityRefs): string {
  if (value === null || value === undefined || value === '') return '—';
  switch (field) {
    case 'due_date':
      return fmtDate(String(value));
    case 'priority':
      return TASK_PRIORITY_LABELS[String(value)] || String(value);
    // Тип задачи как свойство убран (ТЗ 2026-08-28) вместе со справочником
    // типов, поэтому расшифровать id больше нечем. Ветку оставляем, чтобы в
    // старых записях он читался как ссылка на удалённую сущность («#3»), а не
    // как голое число неизвестно чего.
    case 'task_type_id':
      return `#${value}`;
    case 'student_id': {
      const found = (refs.students || []).find((s) => s.id === Number(value));
      return found?.full_name ?? `#${value}`;
    }
    case 'group_id': {
      const found = (refs.groups || []).find((g) => g.id === Number(value));
      return found?.name ?? `#${value}`;
    }
    default:
      return String(value);
  }
}

/**
 * Строка ленты истории — фраза на естественном языке («изменил(-а) срок
 * на 3 сентября»). Комментарии сюда не попадают: они живут отдельным блоком
 * панели, поэтому ветки на `comment` здесь нет.
 *
 * Имя автора и время рисует ActivityRow, здесь только глагольная часть фразы.
 * Глаголы в скобочной форме («создал(-а)»): пола сотрудника в учётке нет, а
 * угадывать его по имени нельзя — ошибка задевает живого человека.
 */
function ActivityLine({ item, stages, assignees, refs }: {
  item: TaskActivityItem;
  stages: TaskStage[] | undefined;
  assignees: TaskAssignee[] | undefined;
  refs: ActivityRefs;
}) {
  const meta = item.meta || {};
  let phrase: ReactNode;
  switch (item.kind) {
    case 'stage_change': {
      const resolution = meta.resolution ? TASK_RESOLUTION_LABELS[String(meta.resolution)] : null;
      // У закрытия суть — результат, а не название стадии; откуда перенесли не
      // пишем: предыдущая стадия видна соседней строкой ленты.
      phrase = resolution
        ? <>закрыл(-а) задачу: {resolution}</>
        : <>перенёс(-ла) задачу в «{stageLabelOf(stages, meta.to_stage_id)}»</>;
      break;
    }
    case 'assign': {
      // Записи ленты бывают ДВУХ форматов, и понимать надо оба. До перехода на
      // нескольких исполнителей бэкенд писал одиночный to_assignee_id; эти
      // записи уже лежат в базе и переписать их нельзя — прочитав только новый
      // ключ, мы бы задним числом опустошили всю прежнюю историю назначений.
      const to = Array.isArray(meta.to_assignee_ids)
        ? (meta.to_assignee_ids as unknown[]).map(Number)
        : (meta.to_assignee_id == null ? [] : [Number(meta.to_assignee_id)]);
      if (to.length === 0) { phrase = <>снял(-а) исполнителей</>; break; }
      // author_id и id исполнителя — оба id учётки (accounts.Account), сравнение
      // корректно: «назначил(-а) себя» читается короче, чем имя дважды в строке.
      phrase = to.length === 1 && item.author_id != null && to[0] === item.author_id
        ? <>назначил(-а) себя исполнителем</>
        : (
          <>
            назначил(-а) исполнителями:{' '}
            {to.map((aid) => assigneeLabelOf(assignees, aid)).join(', ')}
          </>
        );
      break;
    }
    case 'system': {
      const field = meta.field ? String(meta.field) : null;
      if (!field) {
        // Создание задачи и смена меток приходят с готовым text и без meta.field;
        // различаем их по meta (см. taskboard/services.py: create_task пишет
        // stage_id, set_tags — tag_ids). Готовый text во фразу от лица автора не
        // встаёт — «Илья Павлов Теги: дизайн», — поэтому собираем свою.
        if (meta.stage_id != null) { phrase = <>создал(-а) задачу</>; break; }
        // Метки как свойство задачи убраны (ТЗ 2026-08-28), но записи об их
        // правках остались в базе, и переписать их нельзя. Ветку держим ради
        // старой истории: без неё эти строки показали бы пустоту.
        if (meta.tag_ids != null) {
          const labels = String(item.text ?? '').replace(/^Теги:\s*/, '');
          phrase = <>изменил(-а) метки: {labels || 'нет'}</>;
          break;
        }
        phrase = <>{item.text}</>;
        break;
      }
      const what = TASK_FIELD_PHRASES[field] || field;
      // Описание значением в ленту не пишем: оно бывает на абзац, и строка
      // истории распухает. Сам факт правки важнее её содержимого.
      if (field === 'description') { phrase = <>изменил(-а) описание</>; break; }
      const to = meta.to;
      phrase = (to === null || to === undefined || to === '')
        ? <>убрал(-а) {what}</>
        : <>изменил(-а) {what} на {formatFieldValue(field, to, refs)}</>;
      break;
    }
    default:
      phrase = <>{item.text}</>;
  }
  return (
    <ActivityRow
      authorName={item.author_name}
      time={fmtRelativeDateTime(item.created_at)}
    >
      {phrase}
    </ActivityRow>
  );
}

/**
 * Панель карточки задачи (спека 2026-08-24/26, переработка ТЗ 2026-08-26).
 *
 * Это карточка просмотра, а не форма: свойства стоят строками (InlineField) и
 * правятся прямо в них — контрол на месте всегда, но рамку и подложку надевает
 * только под курсором. Отдельного «Сохранить» нет — правка уходит на сервер
 * сразу, как и в карточке сделки.
 */
export function TaskDrawer({ id, onClose }: Props) {
  const { data: task, isLoading } = useTask(id);
  const { data: activity, isLoading: activityLoading } = useTaskActivity(id);
  const { data: assignees } = useTaskAssignees();
  const { data: stages } = useTaskStages(task?.board_id);
  // Панель рендерится только пока карточка открыта (TasksPage монтирует
  // TaskDrawer условно на selectedId) — сами хуки закрывают запрос, когда
  // компонент размонтирован, дополнительный `enabled` не нужен. Список уже
  // наверняка в кэше: тот же useStudentsAll дёргает PaymentModal.
  const { data: students } = useStudentsAll();
  const { data: groups } = useGroupsAll();
  const { update, complete, comment, create, remove } = useTaskMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const { me } = useAuth();

  const [title, setTitle] = useState('');
  const [titleEditing, setTitleEditing] = useState(false);
  const [description, setDescription] = useState('');
  const [descriptionOpen, setDescriptionOpen] = useState(false);
  const [assigneeIds, setAssigneeIds] = useState<number[]>([]);
  const [commentText, setCommentText] = useState('');
  const [completeOpen, setCompleteOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Пользовательская ширина панели (задача 2026-08-26, вынесено в общий хук
  // задачей «ручка ресайза для двух панелей», см. hooks/useDrawerResize.ts).
  const {
    width: drawerWidth, resizing, handleProps: resizeHandleProps, wrapOverlayClose,
  } = useDrawerResize({ storageKey: TASK_DRAWER_WIDTH_KEY });
  const handleOverlayClick = wrapOverlayClose(onClose);

  // Буфер полей ре-синхронизируется только при смене самой карточки (id), не на
  // каждый рефетч — иначе набираемый текст затиралось бы фоновой инвалидацией
  // (тот же повод, что держит outcomeDate в RenewalDrawer отдельным state).
  // Исполнители — ровно тот же случай: их отмечают по нескольку подряд,
  // и набор считается от буфера, а не от task.assignees.
  useEffect(() => {
    setTitle(task?.title ?? '');
    setDescription(task?.description ?? '');
    setAssigneeIds(task?.assignees.map((a) => a.id) ?? []);
    setTitleEditing(false);
    setDescriptionOpen(false);
  }, [task?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleEscape = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    window.addEventListener('keydown', handleEscape);
    return () => window.removeEventListener('keydown', handleEscape);
  }, [handleEscape]);

  const save = (body: Record<string, unknown>) => {
    if (!task) return;
    update.mutate({ id: task.id, ...body }, {
      onError: (err) => showError(conflictError(err), 'Не удалось сохранить изменение'),
    });
  };

  const handleTitleBlur = () => {
    setTitleEditing(false);
    if (!task) return;
    const trimmed = title.trim();
    if (!trimmed) { setTitle(task.title); return; }
    if (trimmed === task.title) return;
    save({ title: trimmed });
  };

  const handleDescriptionBlur = () => {
    if (!task) return;
    const trimmed = description.trim();
    const current = task.description ?? '';
    if (trimmed === current) return;
    save({ description: trimmed || null });
  };

  // Набор исполнителей MultiSelect считает от переданных values, то есть от
  // локального буфера, — здесь остаётся только запомнить его и отправить.
  // Пустой массив = снять всех, отдельного «отвязать» серверу не нужно.
  const handleAssigneesChange = (next: number[]) => {
    setAssigneeIds(next);
    save({ assignee_ids: next });
  };

  /**
   * Ссылка на задачу в буфер — чтобы кинуть её коллеге в чат.
   *
   * Собираем адрес хелпером, а не берём текущий href: в нём висят фильтры
   * доски, и получатель открыл бы карточку через чужой набор фильтров.
   * Копирование — тем же приёмом, что invite-ссылка в разделе «Учётки»
   * (AccountsPage): clipboard + подтверждение тостом.
   */
  const handleCopyLink = () => {
    if (!task) return;
    const url = `${window.location.origin}${taskPath(task.board_id, task.id)}`;
    navigator.clipboard.writeText(url).then(
      () => toast('Ссылка скопирована', 'ok'),
      () => toast('Не удалось скопировать', 'error'),
    );
  };

  const handleAddComment = () => {
    const body = commentText.trim();
    if (!body) return;
    comment.mutate({ id, body }, {
      onSuccess: () => setCommentText(''),
      onError: (err) => showError(conflictError(err), 'Не удалось добавить комментарий'),
    });
  };

  const handleComplete = (resolution: TaskResolution) => {
    if (!task) return;
    complete.mutate({ id: task.id, resolution }, {
      onSuccess: () => setCompleteOpen(false),
      onError: (err) => {
        setCompleteOpen(false);
        showError(conflictError(err), 'Не удалось закрыть задачу');
      },
    });
  };

  // Копия уходит в ту же воронку; стадию не передаём — бэкенд сам кладёт новую
  // задачу в первую открытую стадию (создать задачу сразу закрытой он не даёт).
  const handleDuplicate = () => {
    if (!task) return;
    create.mutate({
      board_id: task.board_id,
      title: `${task.title} (копия)`,
      description: task.description,
      assignee_ids: task.assignees.map((a) => a.id),
      student_id: task.student_id,
      group_id: task.group_id,
      due_date: task.due_date,
      priority: task.priority,
    }, {
      onSuccess: () => toast('Копия задачи создана', 'ok'),
      onError: (err) => showError(conflictError(err), 'Не удалось скопировать задачу'),
    });
  };

  const handleDelete = () => {
    if (!task) return;
    remove.mutate(task.id, {
      onSuccess: () => {
        setDeleteOpen(false);
        toast('Задача удалена', 'ok');
        onClose();
      },
      onError: (err) => {
        setDeleteOpen(false);
        showError(conflictError(err), 'Не удалось удалить задачу');
      },
    });
  };

  const canDelete = canDeleteTask(me?.role as Role);

  // «Изменить» и «Перенести» из ТЗ здесь сознательно не заводим: правка полей
  // стала inline прямо в панели (InlineField), а перенос между стадиями делается
  // перетаскиванием карточки на доске. Отдельные пункты меню дублировали бы уже
  // существующие пути и неизбежно разошлись бы с ними по поведению.
  const menuItems: ActionMenuItem[] = [
    { label: 'Дублировать', onSelect: handleDuplicate },
    {
      label: 'Удалить',
      danger: true,
      disabled: !canDelete,
      // Пункт не прячем, а гасим: иначе менеджер не поймёт, что удаление вообще
      // существует, и будет искать его вместо того, чтобы попросить админа.
      hint: canDelete ? undefined : 'Удалять задачи может только администратор',
      onSelect: () => setDeleteOpen(true),
    },
  ];

  // Пустого варианта тут нет, в отличие от одиночных полей ниже: «снять всех» —
  // это снятые галочки, отдельным пунктом списка оно было бы лишь ещё одним
  // способом сделать то же самое.
  const assigneeOptions = (assignees || [])
    .map((a) => ({ value: a.id, label: assigneeLabel(a) }));

  const priorityOptions = Object.entries(TASK_PRIORITY_LABELS)
    .map(([value, label]) => ({ value, label }));

  // Пустой вариант — не техническая заглушка, а осмысленное действие «отвязать»:
  // задача могла быть заведена не про конкретного ученика/группу или связь
  // потеряла смысл после переноса. Как в assigneeOptions выше.
  const studentOptions = useMemo(() => {
    const opts = [{ value: '', label: '— не выбран —' }];
    if (!students) return opts;
    return opts.concat(
      students
        .slice()
        .sort((a, b) => a.full_name.localeCompare(b.full_name))
        .map((s) => ({ value: String(s.id), label: studentOptionLabel(s) })),
    );
  }, [students]);

  const groupOptions = useMemo(() => {
    const opts = [{ value: '', label: '— не выбрана —' }];
    if (!groups) return opts;
    return opts.concat(
      groups
        .slice()
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((g) => ({ value: String(g.id), label: g.name })),
    );
  }, [groups]);

  // Один объект справочников на всю ленту — из уже загруженных списков.
  const activityRefs: ActivityRefs = useMemo(
    () => ({ students, groups }), [students, groups]);

  // Комментарии и системные события больше не идут одной лентой: читают их
  // по-разному — комментарии как переписку, историю как журнал правок.
  const comments = (activity || []).filter((a) => a.kind === 'comment');
  const history = (activity || []).filter((a) => a.kind !== 'comment');

  return (
    <div className="task-drawer-overlay" onClick={handleOverlayClick}>
      <aside
        className="task-drawer"
        style={{ width: drawerWidth }}
        role="dialog"
        aria-modal="true"
        aria-label="Карточка задачи"
        onClick={(e) => e.stopPropagation()}
      >
        <DrawerResizeHandle resizing={resizing} {...resizeHandleProps} />
        {isLoading || !task ? (
          <div className="task-drawer__loading">Загружаем задачу…</div>
        ) : (
          <>
            <header className="task-drawer__head">
              <div className="task-drawer__head-top">
                <span className="task-drawer__id">#{task.id}</span>
                <button
                  type="button"
                  className="task-drawer__link-btn"
                  onClick={handleCopyLink}
                  title="Скопировать ссылку на задачу"
                  aria-label="Скопировать ссылку на задачу"
                >
                  <LinkGlyph />
                </button>
                <span className="task-drawer__head-spacer" />
                <ActionMenu items={menuItems} label="Действия с задачей" />
                <button
                  type="button"
                  className="task-drawer__close"
                  onClick={onClose}
                  aria-label="Закрыть"
                >
                  ✕
                </button>
              </div>

              {/* Действие и состояние — над заголовком, автор с датой постановки
                  прижат вправо: «кто поставил» относится к задаче целиком, а не
                  к её свойствам, и в списке полей ниже он читался как ещё одно
                  редактируемое поле, хотя менять его нельзя. */}
              <div className="task-drawer__head-actions">
                {task.is_closed ? (
                  <span className={`status-badge status-badge--${
                    task.resolution === 'done' ? 'positive'
                      : task.resolution === 'cancelled' ? 'negative' : 'muted'
                  }`}
                  >
                    {task.resolution ? TASK_RESOLUTION_LABELS[task.resolution] : task.stage_label}
                  </span>
                ) : (
                  <Button variant="primary" size="sm" onClick={() => setCompleteOpen(true)}>
                    Выполнить
                  </Button>
                )}

                <span className="task-drawer__status">
                  <span
                    className="task-drawer__status-dot"
                    style={{ background: stageTone(task.stage_color, task.stage_label).bg }}
                    aria-hidden="true"
                  />
                  <span className="task-drawer__status-label">{task.stage_label}</span>
                </span>

                <span className="task-drawer__author">
                  <Avatar name={task.created_by_name || '—'} size={22} />
                  <span className="task-drawer__author-text">
                    <span className="task-drawer__author-name">
                      {task.created_by_name || '—'}
                    </span>
                    <span className="task-drawer__author-date">
                      {fmtDateTime(task.created_at)}
                    </span>
                  </span>
                </span>
              </div>

              {titleEditing ? (
                <TextInput
                  className="task-drawer__title-input"
                  value={title}
                  autoFocus
                  onChange={(e) => setTitle(e.target.value)}
                  onBlur={handleTitleBlur}
                  onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                  aria-label="Заголовок задачи"
                />
              ) : (
                <button
                  type="button"
                  className="task-drawer__title"
                  onClick={() => setTitleEditing(true)}
                  title="Изменить заголовок"
                >
                  {task.title}
                </button>
              )}
            </header>

            <div className="task-drawer__section">
              <div className="task-drawer__section-title">Контекст</div>
              <div className="task-drawer__fields">
                {/* Исполнителей может быть несколько. В покое строка показывает
                    их стопкой аватаров с именами, пустая — приглушённой
                    подсказкой «Назначить…»: прочерк сообщил бы, что данных нет,
                    но промолчал бы о том, что поле заполняемое. */}
                <InlineField icon={<PersonGlyph />} label="Исполнители">
                  <MultiSelect
                    values={assigneeIds}
                    onChange={handleAssigneesChange}
                    options={assigneeOptions}
                    placeholder="Назначить…"
                    emptyText="Некого назначить"
                    renderValue={(selected) => (
                      <span className="task-drawer__assignees">
                        <AvatarStack
                          names={selected.map((o) => o.label)}
                          size={20}
                        />
                        <span className="task-drawer__assignees-names">
                          {selected.map((o) => o.label).join(', ')}
                        </span>
                      </span>
                    )}
                    aria-label="Исполнители"
                  />
                </InlineField>

                {/* Ссылка на карточку — слотом `below`, то есть под контролом и
                    в его же колонке. Соседом строки она встала бы под подпись
                    и разъехалась бы с полем, к которому относится. */}
                <InlineField
                  icon={<StudentGlyph />}
                  label="Ученик"
                  below={task.student_id != null && (
                    <EntityLink
                      section="students"
                      id={task.student_id}
                      text="Открыть карточку →"
                      muted
                    />
                  )}
                >
                  <Combobox
                    value={String(task.student_id ?? '')}
                    onChange={(v) => save({ student_id: v ? Number(v) : null })}
                    options={studentOptions}
                    placeholder="Выбрать ученика…"
                    placeholderValue=""
                    maxVisible={10}
                    aria-label="Ученик"
                  />
                </InlineField>

                <InlineField
                  icon={<GroupGlyph />}
                  label="Группа"
                  below={task.group_id != null && (
                    <EntityLink
                      section="groups"
                      id={task.group_id}
                      text="Открыть карточку →"
                      muted
                    />
                  )}
                >
                  <Combobox
                    value={String(task.group_id ?? '')}
                    onChange={(v) => save({ group_id: v ? Number(v) : null })}
                    options={groupOptions}
                    placeholder="Выбрать группу…"
                    placeholderValue=""
                    maxVisible={10}
                    aria-label="Группа"
                  />
                </InlineField>
              </div>
            </div>

            <div className="task-drawer__section">
              <div className="task-drawer__section-title">Свойства</div>
              <div className="task-drawer__fields">
                {/* Пустой срок показывает свой placeholder приглушённым —
                    DateInput при пустом значении и так рисует его нативным
                    placeholder'ом инпута, отдельный проп не нужен. */}
                <InlineField icon={<CalendarGlyph />} label="Срок">
                  <DateInput
                    value={task.due_date ?? ''}
                    placeholder="Выбрать дату…"
                    onChange={(e) => save({ due_date: e.target.value || null })}
                  />
                </InlineField>

                <InlineField icon={<PriorityGlyph />} label="Приоритет">
                  <SelectInput
                    value={task.priority}
                    onChange={(e) => save({ priority: e.target.value })}
                    options={priorityOptions}
                  />
                </InlineField>

              </div>
            </div>

            <div className="task-drawer__section">
              <div className="task-drawer__section-title">Описание</div>
              {descriptionOpen || task.description ? (
                <Textarea
                  className="task-drawer__description-input"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  onBlur={handleDescriptionBlur}
                  rows={4}
                  placeholder="Описание задачи…"
                />
              ) : (
                <button
                  type="button"
                  className="task-drawer__add-description"
                  onClick={() => setDescriptionOpen(true)}
                >
                  Добавить описание…
                </button>
              )}
            </div>

            {/* «Поставил» и «Создана» уехали в шапку — здесь остаётся только то,
                чего там нет: сколько карточка висит в текущей стадии. */}
            <div className="task-drawer__section task-drawer__meta">
              <div className="task-drawer__meta-row">
                <span>В стадии с</span>
                <span>{fmtDateTime(task.stage_entered_at)}</span>
              </div>
            </div>

            <div className="task-drawer__lower">
              <div className="task-drawer__section">
                <CollapsibleSection title="Комментарии">
                  {activityLoading ? (
                    <div className="task-drawer__loading">Загружаем комментарии…</div>
                  ) : (
                    <CommentThread
                      items={comments.map((c) => ({
                        id: c.id,
                        author: c.author_name,
                        iso: c.created_at,
                        text: c.text,
                      }))}
                      emptyText="Пока нет комментариев"
                    />
                  )}
                  <Textarea
                    className="task-drawer__comment-input"
                    value={commentText}
                    onChange={(e) => setCommentText(e.target.value)}
                    placeholder="Комментарий…"
                    rows={2}
                  />
                  {/* Кнопка появляется только с набранным текстом: пустое поле —
                      просто поле. Совсем прятать действие нельзя — человек должен
                      видеть, чем отправить. */}
                  {commentText.trim() && (
                    <Button
                      className="task-drawer__comment-send"
                      variant="secondary"
                      disabled={comment.isPending}
                      onClick={handleAddComment}
                    >
                      Добавить
                    </Button>
                  )}
                </CollapsibleSection>
              </div>

              <div className="task-drawer__section task-drawer__timeline-section">
                <CollapsibleSection title="История">
                  {activityLoading ? (
                    <div className="task-drawer__loading">Загружаем историю…</div>
                  ) : (
                    <ActivityLog>
                      {/* Новые сверху: свежая правка нужнее, чем создание задачи. */}
                      {history.slice().reverse().map((item) => (
                        <ActivityLine
                          key={item.id}
                          item={item}
                          stages={stages}
                          assignees={assignees}
                          refs={activityRefs}
                        />
                      ))}
                      {history.length === 0 && (
                        <ActivityEmpty>Пока нет истории</ActivityEmpty>
                      )}
                    </ActivityLog>
                  )}
                </CollapsibleSection>
              </div>
            </div>

            {completeOpen && (
              <TaskCompleteDialog
                open
                pending={complete.isPending}
                onClose={() => setCompleteOpen(false)}
                onConfirm={handleComplete}
              />
            )}

            {deleteOpen && (
              <ConfirmModal
                title="Удалить задачу?"
                message={`Задача «${task.title}» исчезнет вместе со всей историей и комментариями. Восстановить её будет нельзя.`}
                confirmLabel="Удалить"
                danger
                isPending={remove.isPending}
                onConfirm={handleDelete}
                onClose={() => setDeleteOpen(false)}
              />
            )}
          </>
        )}
      </aside>
    </div>
  );
}

/** Звено цепи — копирование ссылки на задачу. */
function LinkGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 13.5a4 4 0 0 0 5.7.2l3-3a4 4 0 0 0-5.7-5.7l-1.7 1.7" />
      <path d="M14 10.5a4 4 0 0 0-5.7-.2l-3 3a4 4 0 0 0 5.7 5.7l1.7-1.7" />
    </svg>
  );
}
