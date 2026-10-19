import {
  useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode,
} from 'react';
import { Checkbox } from './Checkbox';
import { Floating } from './Floating';

export interface MultiSelectOption {
  value: number;
  label: string;
}

interface Props {
  /** Выбранные значения. Держать их СЛЕДУЕТ в буфере вызывающего — см. ниже. */
  values: number[];
  options: MultiSelectOption[];
  /**
   * Новый набор целиком, а не «включили/выключили»: вызывающий всё равно
   * обязан отправить на сервер весь набор, и считать его дважды незачем.
   *
   * Набор приходит посчитанным от `values`, то есть от буфера вызывающего.
   * Если держать выбор прямо в серверных данных, два быстрых клика подряд
   * посчитают второй набор от ответа, который ещё не пришёл, и первая правка
   * потеряется (та же грабля, что у меток задачи, см. TaskDrawer).
   */
  onChange: (next: number[]) => void;
  /** Подсказка пустого поля — приглушённая, как у Combobox. */
  placeholder?: string;
  /**
   * Как показать выбранных в покое. По умолчанию — подписи через запятую;
   * панель задачи подставляет сюда стопку аватаров с именами.
   */
  renderValue?: (selected: MultiSelectOption[]) => ReactNode;
  /** Что написать в выпадашке, когда выбирать не из кого. */
  emptyText?: string;
  /** Сколько строк помещается в выпадашке; остальное прокручивается. */
  maxVisible?: number;
  'aria-label'?: string;
}

/**
 * Выбор НЕСКОЛЬКИХ значений из списка: в покое — сводка выбранного, по клику —
 * список с галочками, каждая правка уходит наружу сразу.
 *
 * Отдельный компонент, а не Combobox с multiple: у Combobox поле ввода и одно
 * выбранное значение в нём — при нескольких выбранных показывать в input нечего,
 * а выбор там закрывает список, тогда как здесь людей отмечают по нескольку
 * подряд, и закрываться после каждого клика список не должен.
 *
 * Триггер — button, а не input: набирать в нём нечего, поиска нет (список
 * сотрудников короткий), а кнопка сама даёт клавиатуру и роль.
 */
export function MultiSelect({
  values, options, onChange, placeholder, renderValue, emptyText = 'Список пуст',
  maxVisible = 8, 'aria-label': ariaLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(
    () => options.filter((o) => values.includes(o.value)),
    [options, values],
  );

  // Click outside → close. Список живёт в портале (вне триггера), поэтому
  // проверяем оба узла — тот же приём, что в Combobox.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!triggerRef.current?.contains(t) && !popoverRef.current?.contains(t)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const toggle = (value: number) => {
    onChange(values.includes(value) ? values.filter((v) => v !== value) : [...values, value]);
  };

  // Escape закрывает список, а не панель вокруг него: у панели задачи свой
  // обработчик на window, и без stopPropagation один Escape закрыл бы обе.
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== 'Escape' || !open) return;
    e.stopPropagation();
    setOpen(false);
    triggerRef.current?.focus();
  };

  return (
    <div className={`multi-select${open ? ' is-open' : ''}`}>
      <button
        ref={triggerRef}
        type="button"
        className="multi-select__trigger"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onKeyDown}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={`multi-select__value${selected.length === 0 ? ' is-placeholder' : ''}`}>
          {selected.length === 0
            ? placeholder
            : (renderValue
              ? renderValue(selected)
              : selected.map((o) => o.label).join(', '))}
        </span>
      </button>

      <Floating
        anchorRef={triggerRef}
        floatingRef={popoverRef}
        open={open}
        className="floating-popover"
        maxHeight={maxVisible * 32 + 8}
      >
        <div className="multi-select__list" onKeyDown={onKeyDown}>
          {options.length === 0 ? (
            <div className="multi-select__empty">{emptyText}</div>
          ) : (
            options.map((opt) => (
              <Checkbox
                key={opt.value}
                className="multi-select__item"
                checked={values.includes(opt.value)}
                onChange={() => toggle(opt.value)}
                label={opt.label}
              />
            ))
          )}
        </div>
      </Floating>
    </div>
  );
}
