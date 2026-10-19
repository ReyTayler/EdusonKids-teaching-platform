import { useAuth } from '../../hooks/useAuth';

/** Ключи фильтра, которыми управляют сегменты. */
const SEGMENT_KEYS = ['assignee_id', 'due', 'overdue'] as const;

interface Props {
  /** Текущие значения из URL: ключ → строка. */
  values: Record<string, string>;
  /** Записать набор ключей разом; пустая строка удаляет ключ. */
  onApply: (patch: Record<string, string>) => void;
}

/**
 * Быстрые представления. Своего состояния НЕ заводят: сегмент — это ярлык,
 * который пишет обычные ключи фильтра, а подсветка выводится из них обратно.
 * Отдельный ключ вида ?seg= был бы вторым источником истины и разъехался бы с
 * popover'ом «Фильтры».
 */
export function TaskSegments({ values, onApply }: Props) {
  const { me } = useAuth();
  const myId = me?.account_id != null ? String(me.account_id) : '';

  const clear: Record<string, string> = Object.fromEntries(
    SEGMENT_KEYS.map((k) => [k, '']),
  );

  const segments = [
    { key: 'all', label: 'Все', patch: clear,
      active: SEGMENT_KEYS.every((k) => !values[k]) },
    { key: 'mine', label: 'Мои', patch: { ...clear, assignee_id: myId },
      active: !!myId && values.assignee_id === myId },
    { key: 'today', label: 'Сегодня', patch: { ...clear, due: 'today' },
      active: values.due === 'today' },
    { key: 'overdue', label: 'Просроченные', patch: { ...clear, overdue: 'true' },
      active: values.overdue === 'true' },
  ];

  return (
    <div className="task-segments" role="group" aria-label="Быстрые представления">
      {segments.map((s) => (
        <button
          key={s.key}
          type="button"
          className={`task-segments__btn${s.active ? ' is-active' : ''}`}
          aria-pressed={s.active}
          onClick={() => onApply(s.patch)}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
