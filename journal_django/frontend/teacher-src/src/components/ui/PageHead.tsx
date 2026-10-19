import type { ReactNode } from 'react';

interface Props {
  /** Заголовок страницы (h2 18px/600). */
  title: ReactNode;
  /** Мелкое пояснение справа (серым). */
  sub?: ReactNode;
  /** Действия справа: переключатель месяца, кнопки. */
  children?: ReactNode;
  /** Что стоит над заголовком: «← Назад», хлебные крошки. */
  lead?: ReactNode;
}

/**
 * Шапка страницы кабинета по паттерну Eduson: заголовок слева, справа
 * пояснение и/или действия. Общая для всех экранов преподавателя, чтобы
 * заголовок не был свойством то календаря, то таблицы.
 */
export function PageHead({ title, sub, children, lead }: Props) {
  return (
    <div className="t-head">
      <div className="t-head__main">
        {lead}
        <h2 className="t-head__title">{title}</h2>
      </div>
      {sub != null && <div className="t-head__sub">{sub}</div>}
      {children != null && <div className="t-head__actions">{children}</div>}
    </div>
  );
}
