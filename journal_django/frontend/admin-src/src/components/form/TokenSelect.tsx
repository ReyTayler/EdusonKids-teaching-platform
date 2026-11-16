import {
  useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent,
} from 'react';
import { Floating } from './Floating';

export interface TokenOption {
  value: number;
  label: string;
}

interface Props {
  /** Выбранные значения — держать в буфере вызывающего (см. MultiSelect). */
  values: number[];
  options: TokenOption[];
  /** Новый набор целиком, а не «добавили/убрали». */
  onChange: (next: number[]) => void;
  placeholder?: string;
  emptyText?: string;
  /** Только чтение: бейджи без «×», ввода и списка нет. */
  disabled?: boolean;
  'aria-label': string;
}

/** «ё» и «е» в поиске равны: преподавателя «Фёдоров» ищут и так, и так. */
const norm = (s: string) => s.toLowerCase().replace(/ё/g, 'е');

const ROW_H = 34;
const MAX_VISIBLE = 8;

/**
 * Выбор нескольких значений с поиском: выбранные лежат бейджами прямо в поле,
 * ввод — в конце той же строки. Список не закрывается после выбора, потому что
 * людей добавляют по нескольку подряд.
 *
 * Отдельно от MultiSelect: там нет ни поиска, ни бейджей, а триггер — кнопка,
 * в которой нельзя печатать. Выбранные скрыты из выпадашки, поэтому убрать
 * значение можно только через «×» бейджа (или Backspace в пустом поле).
 */
export function TokenSelect({
  values, options, onChange, placeholder, emptyText = 'Ничего не найдено',
  disabled = false, 'aria-label': ariaLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();

  // Порядок бейджей — порядок выбора, а не порядок options: видно, что добавлено последним.
  const selected = useMemo(
    () => values
      .map((v) => options.find((o) => o.value === v))
      .filter((o): o is TokenOption => Boolean(o)),
    [values, options],
  );

  const available = useMemo(() => {
    const q = norm(query.trim());
    return options.filter(
      (o) => !values.includes(o.value) && (!q || norm(o.label).includes(q)),
    );
  }, [options, values, query]);

  // Список мог сжаться фильтром или выбором — подсветка не должна уйти за его конец.
  const active = Math.min(highlight, Math.max(0, available.length - 1));

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: globalThis.MouseEvent) => {
      const t = e.target as Node;
      // Список живёт в портале (вне контейнера) — проверяем оба узла.
      if (!containerRef.current?.contains(t) && !popoverRef.current?.contains(t)) {
        setOpen(false);
        setQuery('');
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const add = (opt: TokenOption) => {
    onChange([...values, opt.value]);
    setQuery('');
    setHighlight(0);
  };
  const remove = (value: number) => onChange(values.filter((v) => v !== value));

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setHighlight(Math.min(available.length - 1, active + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight(Math.max(0, active - 1));
    } else if (e.key === 'Enter') {
      // Enter не должен отправлять окружающую форму, пока список открыт.
      if (open) {
        e.preventDefault();
        if (available[active]) add(available[active]);
      }
    } else if (e.key === 'Escape') {
      // stopPropagation: закрываем список, а не модалку/панель вокруг.
      if (open) { e.stopPropagation(); setOpen(false); setQuery(''); }
    } else if (e.key === 'Backspace') {
      if (query === '' && values.length > 0) remove(values[values.length - 1]);
    } else if (e.key === 'Tab') {
      setOpen(false);
      setQuery('');
    }
  };

  // Клик по пустому месту строки отдаёт фокус полю ввода. Кнопки «×» фокус не
  // перехватывают, иначе они теряли бы его раньше, чем сработает click.
  const onRowMouseDown = (e: MouseEvent<HTMLDivElement>) => {
    if (disabled) return;
    if ((e.target as HTMLElement).closest('button')) { e.preventDefault(); return; }
    if (e.target !== inputRef.current) e.preventDefault();
    inputRef.current?.focus();
    setOpen(true);
  };

  const activeId = open && available[active] ? `${listId}-${available[active].value}` : undefined;

  return (
    <div
      ref={containerRef}
      className={`token-select${open ? ' is-open' : ''}${disabled ? ' is-disabled' : ''}`}
      onMouseDown={onRowMouseDown}
    >
      <div className="token-select__row">
        {selected.map((o) => (
          <span key={o.value} className="token-select__token">
            <span className="token-select__token-label">{o.label}</span>
            {!disabled && (
              <button
                type="button"
                className="token-select__token-remove"
                aria-label={`Убрать «${o.label}»`}
                onClick={() => remove(o.value)}
              >
                ×
              </button>
            )}
          </span>
        ))}
        {!disabled && (
          <input
            ref={inputRef}
            type="text"
            className="token-select__input"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); setHighlight(0); }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
            placeholder={selected.length === 0 ? placeholder : undefined}
            autoComplete="off"
            role="combobox"
            aria-label={ariaLabel}
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeId}
          />
        )}
        {disabled && selected.length === 0 && (
          <span className="token-select__placeholder">{placeholder}</span>
        )}
      </div>
      {!disabled && (
        <div className="token-select__tools">
          {selected.length > 0 && (
            <button
              type="button"
              className="token-select__clear"
              aria-label="Очистить всё"
              onClick={() => { onChange([]); inputRef.current?.focus(); }}
            >
              ×
            </button>
          )}
          <span className="token-select__chevron" aria-hidden="true" />
        </div>
      )}

      <Floating
        anchorRef={containerRef}
        floatingRef={popoverRef}
        open={open && !disabled}
        className="floating-popover"
        maxHeight={MAX_VISIBLE * ROW_H + 8}
      >
        <ul ref={listRef} id={listId} className="combobox__list" role="listbox" aria-multiselectable="true">
          {available.length === 0 ? (
            <li className="combobox__empty">{emptyText}</li>
          ) : (
            available.map((opt, i) => (
              <li
                key={opt.value}
                id={`${listId}-${opt.value}`}
                role="option"
                aria-selected={false}
                className={`combobox__item${i === active ? ' is-highlighted' : ''}`}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => { e.preventDefault(); add(opt); }}
              >
                <span className="combobox__item-label">{opt.label}</span>
              </li>
            ))
          )}
        </ul>
      </Floating>
    </div>
  );
}
