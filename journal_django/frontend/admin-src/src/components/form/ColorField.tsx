import { useId } from 'react';

/** Готовая палитра: заметно различимые оттенки, чтобы направления не сливались
 *  в списках и в календаре. Контраст с белым фоном у всех ≥ 3:1. Первые четыре —
 *  цвета курсов Eduson (--t-scratch/python/vibe/groups): выбранный из них цвет
 *  совпадает с токенами курса. Значение хранится в БД как hex, поэтому здесь
 *  литералы, а не var(). */
const PRESETS = [
  '#c2410c', '#1a56db', '#067a57', '#a21570',
  '#0d9488', '#b45309', '#dc2626', '#7c3aed',
];

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** Разрешить «без цвета» (кнопка сброса). */
  clearable?: boolean;
}

/**
 * Поле выбора цвета для форм.
 *
 * `ColorInput` — это голый `<input type="color">`; в форме направления он
 * растягивался на всю ширину и выглядел как непонятная полоса: стили
 * `.color-field` в forms.css были написаны, но обёртки, к которой они
 * применяются, не существовало.
 *
 * Здесь собран весь набор: образец, редактируемый HEX, готовая палитра и сброс.
 * Палитра нужна, потому что цвет направления — не украшение: по нему различают
 * занятия в календаре, и подбирать его пипеткой наугад неудобно.
 */
export function ColorField({ value, onChange, clearable = true }: Props) {
  const id = useId();
  const current = value || '';
  const isValid = /^#[0-9a-fA-F]{6}$/.test(current);

  return (
    <div className="color-field-wrap">
      <div className="color-field">
        <input
          id={id}
          type="color"
          value={isValid ? current : '#000000'}
          onChange={(e) => onChange(e.target.value)}
          aria-label="Выбрать цвет"
        />
        <input
          type="text"
          className="color-field__hex"
          value={current}
          placeholder="#0d9488"
          maxLength={7}
          spellCheck={false}
          aria-label="HEX-код цвета"
          onChange={(e) => {
            const v = e.target.value.trim();
            onChange(v.startsWith('#') || v === '' ? v : `#${v}`);
          }}
        />
        {clearable && current !== '' && (
          <button
            type="button"
            className="color-field__clear"
            onClick={() => onChange('')}
            aria-label="Убрать цвет"
            title="Убрать цвет"
          >×</button>
        )}
      </div>
      <div className="color-presets" role="group" aria-label="Готовые цвета">
        {PRESETS.map((c) => (
          <button
            key={c}
            type="button"
            className={`color-presets__dot${current.toLowerCase() === c ? ' is-active' : ''}`}
            style={{ background: c }}
            onClick={() => onChange(c)}
            aria-label={`Цвет ${c}`}
            title={c}
          />
        ))}
      </div>
    </div>
  );
}
