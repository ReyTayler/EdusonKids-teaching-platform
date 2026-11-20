import type { ReactNode } from 'react';

/**
 * Строка над списком: слева лупа (или другой контрол поиска), справа действия.
 * Один каркас для всех разделов — отступ до таблицы не «прыгает» между страницами.
 */
export function FilterToolbar({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="filter-toolbar">
      <div className="filter-toolbar__search">{children}</div>
      {actions && <div className="filter-toolbar__actions">{actions}</div>}
    </div>
  );
}
