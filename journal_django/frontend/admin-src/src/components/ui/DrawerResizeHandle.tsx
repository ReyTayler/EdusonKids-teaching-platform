import type { KeyboardEvent, MouseEvent } from 'react';

interface Props {
  onMouseDown: (e: MouseEvent) => void;
  onKeyDown: (e: KeyboardEvent) => void;
  resizing?: boolean;
  label?: string;
}

/**
 * Ручка изменения ширины боковой панели (TaskDrawer, RenewalDrawer). Обработчики
 * приходят от useDrawerResize — сам компонент только рисует и не знает о ширине.
 *
 * Устройство разметки: внешний .drawer-resize-handle — широкая зона захвата
 * мышью (шире видимой линии, иначе в 2px курсором не попасть) и сама тонкая
 * линия (::after на всю высоту); вложенный .drawer-resize-handle__grip — заметный
 * скруглённый штрих по центру высоты, за который цепляется взгляд в состоянии
 * покоя (WEEEK-паттерн, правка 2026-08-26: ручка должна быть видна всегда, не
 * только по hover).
 */
export function DrawerResizeHandle({
  onMouseDown, onKeyDown, resizing = false, label = 'Изменить ширину панели',
}: Props) {
  return (
    <div
      className={`drawer-resize-handle${resizing ? ' is-resizing' : ''}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      onMouseDown={onMouseDown}
      onKeyDown={onKeyDown}
    >
      <span className="drawer-resize-handle__grip" aria-hidden="true" />
    </div>
  );
}
