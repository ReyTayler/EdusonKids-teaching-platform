import { type ReactNode } from 'react';

interface Props {
  /** Иконка 24–28px. Только inline-SVG — эмодзи в системе не используются. */
  icon?: ReactNode;
  /** Главная строка: что именно пусто. */
  children: ReactNode;
  /** Вторая строка: что с этим делать. Пустое состояние без подсказки — тупик. */
  hint?: ReactNode;
  /** Кнопка действия, если отсюда можно сразу что-то создать. */
  action?: ReactNode;
}

/**
 * Пустое состояние списка или блока.
 *
 * Раньше компонент был обёрткой над чужим классом `.memberships__empty` с
 * инлайновым padding и умел показать одну строку текста — поэтому в системе
 * прижилась привычка писать пустые состояния вручную, и на 70 страниц его
 * применили дважды. Теперь у него своя структура: причина, подсказка, действие.
 */
export function EmptyState({ icon, children, hint, action }: Props) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-state__icon" aria-hidden="true">{icon}</div>}
      <p className="empty-state__title">{children}</p>
      {hint && <p className="empty-state__hint">{hint}</p>}
      {action && <div className="empty-state__action">{action}</div>}
    </div>
  );
}
