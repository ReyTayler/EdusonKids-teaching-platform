import type { ReactNode } from 'react';

interface Props {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Видимая подпись справа; без неё обязателен aria-label. */
  label?: ReactNode;
  'aria-label'?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * Переключатель «вкл / выкл» для действий, которые срабатывают сразу, без кнопки
 * «Сохранить». Для выбора внутри формы используйте Checkbox.
 */
export function Switch({ checked, onChange, label, disabled, className, ...rest }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={rest['aria-label']}
      disabled={disabled}
      className={`switch${className ? ` ${className}` : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="switch__track" aria-hidden="true"><span className="switch__thumb" /></span>
      {label && <span className="switch__label">{label}</span>}
    </button>
  );
}
