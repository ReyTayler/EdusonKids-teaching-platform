import type { ReactNode } from 'react';

/**
 * Правая колонка страницы документа — оглавление.
 *
 * Панели свойств здесь больше нет: автор, раздел и дата уже стоят в строке под
 * заголовком, и вторая копия тех же сведений только отнимала место у
 * оглавления.
 */
export function DocumentSide({ children }: { children: ReactNode }) {
  return <aside className="kb-side">{children}</aside>;
}
