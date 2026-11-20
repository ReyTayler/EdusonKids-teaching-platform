import { useMemo } from 'react';
import { TableFilterPanel, type FilterField } from '../../components/filters';
import { useTaskAssignees, useTaskStages } from '../../hooks/useTaskStructure';
import { ROLE_LABELS, TASK_DUE_LABELS, TASK_PRIORITY_LABELS } from '../../lib/labels';
import type { Role } from '../../lib/permissions';
import type { TaskAssignee } from '../../lib/tasks';

interface Props {
  boardId: number;
  /** Плоский снимок фильтров из адреса. */
  values: Record<string, string>;
  /** Записать значения ключей разом; пустая строка снимает ключ. */
  onApply: (patch: Record<string, string>) => void;
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

/**
 * Панель поиска задач (общий модуль components/filters): название, исполнитель,
 * стадия, срок, приоритет, «только открытые». Сегменты («Мои», «Просроченные»)
 * остаются на виду — это быстрые переключатели, а не поля поиска.
 */
export function TaskSearchPanel({ boardId, values, onApply, showStage = true }: Props) {
  const { data: stages } = useTaskStages(boardId ?? undefined);
  const { data: assignees } = useTaskAssignees();

  const fields = useMemo<FilterField[]>(() => {
    const list: FilterField[] = [
      { kind: 'text', key: 'q', label: 'Поиск', placeholder: 'Название, #124, ученик…' },
      {
        kind: 'combobox', key: 'assignee_id', label: 'Исполнитель', allLabel: 'Все исполнители',
        options: (assignees || [])
          .slice()
          .sort((a, b) => assigneeLabel(a).localeCompare(assigneeLabel(b)))
          .map((a) => ({ value: String(a.id), label: assigneeLabel(a) })),
      },
    ];
    if (showStage) {
      list.push({
        kind: 'select', key: 'stage_id', label: 'Стадия', allLabel: 'Любая стадия',
        options: (stages ?? []).map((s) => ({ value: String(s.id), label: s.label })),
      });
    }
    list.push(
      {
        kind: 'select', key: 'due', label: 'Срок', allLabel: 'Любой срок',
        options: Object.entries(TASK_DUE_LABELS).map(([value, label]) => ({ value, label })),
      },
      {
        kind: 'select', key: 'priority', label: 'Приоритет', allLabel: 'Любой приоритет',
        options: Object.entries(TASK_PRIORITY_LABELS).map(([value, label]) => ({ value, label })),
      },
      { kind: 'checkbox', key: 'only_open', label: 'Только открытые' },
    );
    return list;
  }, [assignees, stages, showStage]);

  const apply = (next: Record<string, string>) => {
    // Панель отдаёт только заполненные ключи; пустые нужно явно снять в адресе.
    const patch: Record<string, string> = {};
    for (const f of fields) patch[f.key] = next[f.key] ?? '';
    onApply(patch);
  };

  return <TableFilterPanel fields={fields} filters={values} onApply={apply} label="Поиск задач" />;
}
