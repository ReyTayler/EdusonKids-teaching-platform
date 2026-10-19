import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

export type IconButtonSize = 'sm' | 'md';

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Обязательна: у кнопки без подписи название читает только диктор. */
  label: string;
  icon: ReactNode;
  size?: IconButtonSize;
  /** Нажатое состояние для кнопок-переключателей (инструмент, вид списка). */
  active?: boolean;
}

/**
 * Квадратная кнопка с одной иконкой.
 *
 * Не вариант Button: та рассчитана на подпись с горизонтальными полями, а
 * здесь нужна ровная сетка 28/32 px, в которую встают панели инструментов и
 * переключатели. До этого компонента такие кнопки заводились заново на каждом
 * экране (.kb-tool, .kb-view__btn, .modal-close) и расходились в размерах.
 *
 * active превращается в aria-pressed: для диктора это переключатель, а не
 * просто кнопка.
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    { label, icon, size = 'md', active, className, type = 'button', ...rest },
    ref,
  ) {
    const cls = [
      'ui-iconbtn',
      `ui-iconbtn--${size}`,
      active ? 'is-active' : '',
      className,
    ].filter(Boolean).join(' ');

    return (
      <button
        ref={ref}
        type={type}
        className={cls}
        aria-label={label}
        aria-pressed={active}
        {...rest}
      >
        {icon}
      </button>
    );
  },
);
