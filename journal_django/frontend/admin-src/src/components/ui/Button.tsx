import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconLeft?: ReactNode;
}

/**
 * Переиспользуемая кнопка на дизайн-токенах (см. docs/design-system.md).
 * Визуально согласована с legacy-классами .btn-save/.btn-cancel/.btn-delete —
 * используй этот компонент для нового кода вместо голых классов.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', iconLeft, className, children, type = 'button', ...rest },
  ref
) {
  const cls = ['ui-btn', `ui-btn--${variant}`, `ui-btn--${size}`, className].filter(Boolean).join(' ');
  return (
    <button ref={ref} type={type} className={cls} {...rest}>
      {iconLeft && <span className="ui-btn__icon">{iconLeft}</span>}
      {children}
    </button>
  );
});
