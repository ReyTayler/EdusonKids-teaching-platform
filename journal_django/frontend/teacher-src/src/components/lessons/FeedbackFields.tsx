import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';

/**
 * Поля блока «Обратная связь по уроку». Выбор — radiogroup с «плавающим»
 * tabindex: в группу Tab заходит один раз, внутри ходят стрелками (как у
 * нативных radio, но без нативных элементов — стиль Eduson).
 */

interface RadioGroupProps<V extends string | number | boolean> {
  options: { value: V; label: string }[];
  value: V | null;
  onChange: (v: V) => void;
  ariaLabel: string;
  invalid?: boolean;
  /** Класс контейнера и кнопки задают вид (шкала или сегмент). */
  groupClass: string;
  btnClass: string;
}

function RadioGroup<V extends string | number | boolean>({
  options, value, onChange, ariaLabel, invalid, groupClass, btnClass,
}: RadioGroupProps<V>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  // Пока ничего не выбрано, в фокус-цепочке — первая кнопка.
  const selectedIdx = options.findIndex((o) => o.value === value);
  const tabIdx = selectedIdx >= 0 ? selectedIdx : 0;

  const move = (to: number) => {
    const i = (to + options.length) % options.length;
    onChange(options[i].value);
    refs.current[i]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    switch (e.key) {
      case 'ArrowRight': case 'ArrowDown': e.preventDefault(); move(i + 1); break;
      case 'ArrowLeft': case 'ArrowUp': e.preventDefault(); move(i - 1); break;
      case 'Home': e.preventDefault(); move(0); break;
      case 'End': e.preventDefault(); move(options.length - 1); break;
      default: break;
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      aria-invalid={invalid || undefined}
      className={`${groupClass}${invalid ? ' is-invalid' : ''}`}
    >
      {options.map((o, i) => {
        const checked = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={i === tabIdx ? 0 : -1}
            ref={(el) => { refs.current[i] = el; }}
            className={`${btnClass}${checked ? ' is-active' : ''}`}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

const SCALE = Array.from({ length: 10 }, (_, i) => ({ value: i + 1, label: String(i + 1) }));
const YES_NO = [
  { value: true, label: 'Да' },
  { value: false, label: 'Нет' },
];

/** Обёртка вопроса: подпись, звёздочка обязательности, ошибка под полем. */
export function FeedbackQuestion({ id, label, error, children, field }: {
  id: string;
  label: string;
  error?: string;
  children: ReactNode;
  /** Ключ поля — по нему форма находит первое незаполненное и ставит фокус. */
  field: string;
}) {
  return (
    <div className="lf-fb-q" data-fb-field={field}>
      <label id={id} className="lf-fb-label">{label}<span className="field-req"> *</span></label>
      {children}
      {error && <div className="field-error" role="alert">{error}</div>}
    </div>
  );
}

export function ScaleField({ field, label, value, onChange, error }: {
  field: string;
  label: string;
  value: number | null;
  onChange: (v: number) => void;
  error?: string;
}) {
  const id = useId();
  return (
    <FeedbackQuestion id={id} label={label} error={error} field={field}>
      <RadioGroup
        options={SCALE}
        value={value}
        onChange={onChange}
        ariaLabel={`${label} (от 1 до 10)`}
        invalid={!!error}
        groupClass="lf-scale"
        btnClass="lf-scale__btn"
      />
    </FeedbackQuestion>
  );
}

export function YesNoField({ field, label, value, onChange, error }: {
  field: string;
  label: string;
  value: boolean | null;
  onChange: (v: boolean) => void;
  error?: string;
}) {
  const id = useId();
  return (
    <FeedbackQuestion id={id} label={label} error={error} field={field}>
      <RadioGroup
        options={YES_NO}
        value={value}
        onChange={onChange}
        ariaLabel={label}
        invalid={!!error}
        groupClass="segmented lf-yesno"
        btnClass="segmented__btn"
      />
    </FeedbackQuestion>
  );
}
