import { useState, type ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';

export type TaskViewMode = 'board' | 'week';

interface ViewOption {
  value: TaskViewMode;
  label: string;
  icon: ReactNode;
}

// Порядок как в референсе заказчика: сначала календарный вид, потом доска.
const VIEWS: ViewOption[] = [
  { value: 'week', label: 'Неделя', icon: <WeekGlyph /> },
  { value: 'board', label: 'Доска', icon: <BoardGlyph /> },
];

interface Props {
  value: TaskViewMode;
  onChange: (view: TaskViewMode) => void;
}

/**
 * Выбор представления — кнопкой с выпадающей плиткой, а не рядом переключателей.
 *
 * Раньше это был сегментированный переключатель в правом конце фильтр-бара. Ряд
 * из двух кнопок занимал место наравне с фильтрами, хотя вид переключают редко,
 * а когда представлений станет больше двух (в планах «Список»), ряд бы просто
 * не влез. Кнопка показывает текущий вид и открывает остальные по клику.
 */
export function TaskViewSwitcher({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const current = VIEWS.find((v) => v.value === value) ?? VIEWS[1];

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger className="task-view__trigger" aria-label="Вид раздела">
        <span className="task-view__trigger-icon">{current.icon}</span>
        <span>{current.label}</span>
        <ChevronGlyph />
      </Popover.Trigger>
      <Popover.Portal>
        {/* data-floating-popover — метка для Dialog.onInteractOutside: клик по
            всплывашке не должен закрывать модалку, если та открыта вокруг. */}
        <Popover.Content
          className="task-view__panel"
          data-floating-popover
          align="start"
          sideOffset={6}
        >
          <div className="task-view__grid">
            {VIEWS.map((v) => (
              <button
                key={v.value}
                type="button"
                className={`task-view__tile${v.value === value ? ' is-active' : ''}`}
                aria-pressed={v.value === value}
                onClick={() => { onChange(v.value); setOpen(false); }}
              >
                <span className="task-view__tile-icon">{v.icon}</span>
                <span className="task-view__tile-label">{v.label}</span>
              </button>
            ))}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** Доска: колонки стадий. */
function BoardGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16M15 4v16" />
    </svg>
  );
}

/** Неделя: календарь с выделенной строкой дней. */
function WeekGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
      <path d="M7 14.5h10" />
    </svg>
  );
}

function ChevronGlyph() {
  return (
    <svg className="task-view__chevron" width="12" height="12" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}
