import { type FocusEvent, type ChangeEvent } from 'react';

interface Props {
  value: string; // 'HH:MM' or ''
  onChange: (e: { target: { value: string } }) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * Маскированный ввод времени HH:MM. Нативный <input type="time"> запрещён
 * дизайн-системой (рендерит нестилизуемый OS-виджет picker, ломает тёмную
 * тему) — см. docs/design-system.md. Стиль в духе DateInput/TextInput:
 * контролируемый текстовый инпут, значение всегда либо '' либо 'HH:MM'.
 * onChange-сигнатура — { target: { value } }, как у DateInput/SelectInput
 * (совместимо с существующими setState((e) => e.target.value) вызовами).
 */
export function TimeInput({ value, onChange, placeholder = '--:--', disabled, className }: Props) {
  const format = (raw: string): string => {
    const digits = raw.replace(/\D/g, '').slice(0, 4);
    if (digits.length <= 2) return digits;
    return `${digits.slice(0, 2)}:${digits.slice(2)}`;
  };

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    onChange({ target: { value: format(e.target.value) } });
  };

  // При потере фокуса клампим часы/минуты (23/59) и дополняем нулями — не
  // отправлять на бэкенд заведомо невалидное время (частичный ввод типа '9:').
  const handleBlur = (_e: FocusEvent<HTMLInputElement>) => {
    const m = /^(\d{1,2}):?(\d{0,2})$/.exec(value);
    if (!m || (!m[1] && !m[2])) {
      if (value) onChange({ target: { value: '' } });
      return;
    }
    const h = Math.min(23, Number(m[1] || 0));
    const mm = Math.min(59, Number(m[2] || 0));
    onChange({ target: { value: `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}` } });
  };

  return (
    <input
      type="text"
      inputMode="numeric"
      className={className}
      value={value}
      onChange={handleChange}
      onBlur={handleBlur}
      placeholder={placeholder}
      disabled={disabled}
      maxLength={5}
      autoComplete="off"
    />
  );
}
