import { useMemo } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { Combobox } from '../../components/form/Combobox';
import { Checkbox } from '../../components/form/Checkbox';
import { useAuth } from '../../hooks/useAuth';
import { useTaskAssignees, useTaskStages } from '../../hooks/useTaskStructure';
import { ROLE_LABELS, TASK_DUE_LABELS, TASK_PRIORITY_LABELS } from '../../lib/labels';
import type { Role } from '../../lib/permissions';
import type { TaskAssignee } from '../../lib/tasks';

interface Props {
  boardId: number;
  values: Record<string, string>;
  onSet: (key: string, value: string) => void;
  onReset: () => void;
  /**
   * Показывать поле «Стадия». На доске его быть не должно: фильтр по стадии
   * применяется к счётчикам колонок, но не к их карточкам (вьюха карточек
   * колонки перезаписывает stage_id значением из пути URL) — счётчик соврал бы
   * относительно списка под ним. В недельном виде такого пути нет.
   */
  showStage?: boolean;
}

/** Подпись исполнителя: full_name, а если он пуст — роль. Как в TaskDrawer. */
function assigneeLabel(a: TaskAssignee): string {
  return a.full_name || ROLE_LABELS[a.role as Role] || a.role;
}

// Ключи, которые живут внутри панели. Счётчик на кнопке считает только их:
// сегменты и поиск видны и без раскрытия панели, дублировать их в счётчике
// значило бы показывать «2 фильтра» там, где на виду и так всё.
const KEYS = ['stage_id', 'due', 'priority', 'only_open'];

export function TaskFiltersPopover({ boardId, values, onSet, onReset, showStage = true }: Props) {
  const { me } = useAuth();
  const { data: stages } = useTaskStages(boardId ?? undefined);
  const { data: assignees } = useTaskAssignees();

  // assignee_id в KEYS нет: тот же ключ пишет сегмент «Мои», и он подсвечен на
  // виду — счётчик дублировал бы его. Но выбор КОНКРЕТНОГО человека сегменты не
  // показывают никак, поэтому такой фильтр считаем отдельно: иначе он стал бы
  // невидимым, стоило закрыть панель.
  const myId = me?.account_id != null ? String(me.account_id) : '';
  const activeCount = KEYS.filter((k) => values[k]).length
    + (values.assignee_id && values.assignee_id !== myId ? 1 : 0);

  const assigneeOptions = useMemo(() => [
    { value: '', label: 'Все исполнители' },
    ...(assignees || [])
      .slice()
      .sort((a, b) => assigneeLabel(a).localeCompare(assigneeLabel(b)))
      .map((a) => ({ value: String(a.id), label: assigneeLabel(a) })),
  ], [assignees]);

  // Первый вариант — пустой: он же и способ снять фильтр, отдельного крестика
  // у поля нет (так же устроен фильтр-бар доски).
  const stageOptions = useMemo(() => [
    { value: '', label: 'Любая стадия' },
    ...(stages ?? []).map((s) => ({ value: String(s.id), label: s.label })),
  ], [stages]);

  const priorityOptions = useMemo(() => [
    { value: '', label: 'Любой приоритет' },
    ...Object.entries(TASK_PRIORITY_LABELS).map(([value, label]) => ({ value, label })),
  ], []);

  const dueOptions = useMemo(() => [
    { value: '', label: 'Любой срок' },
    ...Object.entries(TASK_DUE_LABELS).map(([value, label]) => ({ value, label })),
  ], []);

  return (
    <Popover.Root>
      <Popover.Trigger className={`task-filters__trigger${activeCount ? ' is-active' : ''}`}>
        Фильтры{activeCount > 0 && <span className="task-filters__badge">{activeCount}</span>}
      </Popover.Trigger>
      <Popover.Portal>
        {/* data-floating-popover — метка для Dialog.onInteractOutside: клик по
            всплывашке не должен закрывать модалку, если popover открыт внутри неё. */}
        <Popover.Content
          className="task-filters__panel"
          data-floating-popover
          align="end"
          sideOffset={6}
          aria-label="Фильтры задач"
          onInteractOutside={(e) => {
            // Списки SelectInput рендерятся порталом в body — формально это «вне»
            // поповера, и без этой проверки первый же клик по варианту закрывал
            // бы панель. Тот же приём, что в components/ui/Dialog.tsx.
            const t = e.target as HTMLElement | null;
            if (t?.closest('[data-floating-popover]')) e.preventDefault();
          }}
        >
          <Field label="Исполнитель">
            <Combobox
              value={values.assignee_id ?? ''}
              onChange={(v) => onSet('assignee_id', v)}
              options={assigneeOptions}
              placeholder="Все исполнители"
              aria-label="Исполнитель"
            />
          </Field>
          {showStage && (
            <Field label="Стадия">
              <SelectInput
                value={values.stage_id ?? ''}
                onChange={(e) => onSet('stage_id', e.target.value)}
                options={stageOptions}
              />
            </Field>
          )}
          <Field label="Срок">
            <SelectInput
              value={values.due ?? ''}
              onChange={(e) => onSet('due', e.target.value)}
              options={dueOptions}
            />
          </Field>
          <Field label="Приоритет">
            <SelectInput
              value={values.priority ?? ''}
              onChange={(e) => onSet('priority', e.target.value)}
              options={priorityOptions}
            />
          </Field>
          <Checkbox
            label="Только открытые"
            checked={values.only_open === 'true'}
            onChange={(e) => onSet('only_open', e.target.checked ? 'true' : '')}
          />
          <button type="button" className="btn-reset-filters" onClick={onReset}>
            Сбросить
          </button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
