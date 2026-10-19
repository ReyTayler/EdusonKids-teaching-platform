import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

export interface ActionMenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  /** Пункт виден, но недоступен — обычно из-за роли. */
  disabled?: boolean;
  /** Подсказка на пункте: зачем он здесь, если нажать нельзя. */
  hint?: string;
}

interface Props {
  items: ActionMenuItem[];
  /** Доступное имя кнопки-триггера. */
  label?: string;
}

/**
 * Меню вторичных действий («…») для шапок карточек: когда действий больше двух,
 * ряд одинаковых кнопок перестаёт читаться как иерархия.
 *
 * Построено на @radix-ui/react-dropdown-menu (уже в зависимостях, как Dialog/
 * Tabs) — оттуда роли WAI-ARIA, ловушка фокуса, Escape, клик вне и подбор
 * позиции у края экрана.
 */
export function ActionMenu({ items, label = 'Ещё действия' }: Props) {
  if (items.length === 0) return null;

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="action-menu__trigger" aria-label={label}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="5" cy="12" r="1.8" />
          <circle cx="12" cy="12" r="1.8" />
          <circle cx="19" cy="12" r="1.8" />
        </svg>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="action-menu__list" align="end" sideOffset={6}>
          {items.map((it) => (
            <DropdownMenu.Item
              key={it.label}
              className={`action-menu__item${it.danger ? ' is-danger' : ''}`}
              disabled={it.disabled}
              title={it.hint}
              onSelect={it.onSelect}
            >
              {it.label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
