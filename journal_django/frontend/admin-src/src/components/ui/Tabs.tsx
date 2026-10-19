import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

export interface TabItem {
  value: string;
  label: string;
  content: ReactNode;
  disabled?: boolean;
}

interface TabsProps {
  items: TabItem[];
  /** Контролируемое значение. Если не задано — компонент управляет состоянием сам (defaultValue). */
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  className?: string;
}

/**
 * Доступный таб-компонент (WAI-ARIA APG: tablist/tab/tabpanel, roving tabindex,
 * автоактивация по ←/→/Home/End). Панель активного таба монтируется в DOM только
 * при активации — соседние панели не рендерятся, пока на них не переключились.
 */
export function Tabs({ items, value, defaultValue, onChange, className }: TabsProps) {
  const uid = useId();
  const [internal, setInternal] = useState<string | undefined>(defaultValue ?? items[0]?.value);
  const active = value ?? internal ?? items[0]?.value;
  const listRef = useRef<HTMLDivElement>(null);

  const select = (next: string) => {
    if (value === undefined) setInternal(next);
    onChange?.(next);
  };

  const focusTabAt = (idx: number) => {
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    if (!buttons || buttons.length === 0) return;
    buttons[idx]?.focus();
  };

  const enabledIndexes = () => items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const enabled = enabledIndexes();
    if (enabled.length === 0) return;
    const currentIdx = items.findIndex((it) => it.value === active);

    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const dir = e.key === 'ArrowRight' ? 1 : -1;
      const posInEnabled = enabled.indexOf(currentIdx);
      const basePos = posInEnabled === -1 ? 0 : posInEnabled;
      const nextPos = (basePos + dir + enabled.length) % enabled.length;
      const nextIdx = enabled[nextPos];
      select(items[nextIdx].value);
      focusTabAt(nextIdx);
    } else if (e.key === 'Home') {
      e.preventDefault();
      const idx = enabled[0];
      select(items[idx].value);
      focusTabAt(idx);
    } else if (e.key === 'End') {
      e.preventDefault();
      const idx = enabled[enabled.length - 1];
      select(items[idx].value);
      focusTabAt(idx);
    }
  };

  const activeItem = items.find((it) => it.value === active) ?? items[0];

  return (
    <div className={`tabs${className ? ` ${className}` : ''}`}>
      <div ref={listRef} role="tablist" className="tabs__list" onKeyDown={handleKeyDown}>
        {items.map((item) => {
          const selected = item.value === active;
          return (
            <button
              key={item.value}
              type="button"
              role="tab"
              id={`${uid}-tab-${item.value}`}
              aria-selected={selected}
              aria-controls={`${uid}-panel-${item.value}`}
              tabIndex={selected ? 0 : -1}
              disabled={item.disabled}
              className={`tabs__tab${selected ? ' is-active' : ''}`}
              onClick={() => select(item.value)}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {activeItem && (
        <div
          key={activeItem.value}
          role="tabpanel"
          id={`${uid}-panel-${activeItem.value}`}
          aria-labelledby={`${uid}-tab-${activeItem.value}`}
          className="tabs__panel"
          tabIndex={0}
        >
          {activeItem.content}
        </div>
      )}
    </div>
  );
}
