import { useEffect, useMemo, useRef, useState } from 'react';
import type { SelectHTMLAttributes } from 'react';
import { Floating } from './Floating';

interface Option { value: string | number; label: string; }

// Сохраняем onChange-сигнатуру native, чтобы вызовы вида onChange={e => ...e.target.value} продолжали работать.
interface Props extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'onChange'> {
  options: Option[];
  onChange?: (e: { target: { value: string } }) => void;
  placeholder?: string;
  /** Показывать поле поиска. По умолчанию — когда вариантов больше порога. */
  searchable?: boolean;
  /**
   * Значение, которое поле показывает как ПОДСКАЗКУ, а не как выбранный
   * вариант: вместо подписи варианта выводится `placeholder` приглушённым
   * тоном. Сам вариант из `options` не исчезает — иначе поле стало бы
   * неочищаемым (пустой вариант — осмысленное действие «отвязать», а не
   * техническая заглушка).
   *
   * Не задан (по умолчанию) — поведение прежнее: показываем подпись варианта.
   */
  placeholderValue?: string;
}

const SEARCH_THRESHOLD = 7;

export function SelectInput({
  options, value, onChange, placeholder, disabled, className, searchable, placeholderValue, ...rest
}: Props) {
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const currentValue = value !== undefined ? String(value) : '';
  const selected = useMemo(() => options.find((o) => String(o.value) === currentValue), [options, currentValue]);
  const showSearch = searchable ?? options.length > SEARCH_THRESHOLD;
  // Показываем подсказку вместо подписи варианта: либо варианта с таким
  // значением вовсе нет, либо оно объявлено «пустым» через placeholderValue.
  const asPlaceholder = !selected || currentValue === placeholderValue;

  const filtered = useMemo(() => {
    if (!showSearch || !query.trim()) return options;
    const q = query.toLowerCase();
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, query, showSearch]);

  const close = () => { setOpen(false); setQuery(''); };

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      // Список рендерится в портале (вне ref) — исключаем и его.
      if (!ref.current?.contains(t) && !popoverRef.current?.contains(t)) close();
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  // При открытии: сброс поиска, фокус в поле поиска, подсветка на выбранном.
  useEffect(() => {
    if (!open) return;
    setQuery('');
    const idx = options.findIndex((o) => String(o.value) === currentValue);
    setHighlight(idx >= 0 ? idx : 0);
    if (showSearch) searchRef.current?.focus();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Держим подсвеченный пункт в зоне видимости при навигации с клавиатуры.
  useEffect(() => {
    if (!open || !listRef.current) return;
    (listRef.current.children[highlight] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' });
  }, [highlight, open]);

  const choose = (opt: Option) => {
    onChange?.({ target: { value: String(opt.value) } });
    close();
    triggerRef.current?.focus();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setHighlight((h) => Math.min(filtered.length - 1, h + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === 'Enter' || (e.key === ' ' && !showSearch)) {
      e.preventDefault();
      if (!open) setOpen(true);
      else if (filtered[highlight]) choose(filtered[highlight]);
    } else if (e.key === 'Escape') {
      if (open) { e.stopPropagation(); close(); } // закрываем список, не саму модалку
    }
  };

  return (
    <div className={`select-input ${open ? 'is-open' : ''} ${className || ''}`} ref={ref}>
      <button
        type="button"
        ref={triggerRef}
        className="select-input__trigger"
        onClick={() => !disabled && (open ? close() : setOpen(true))}
        onKeyDown={onKey}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={`select-input__value ${asPlaceholder ? 'is-placeholder' : ''}`}>
          {asPlaceholder ? (placeholder || 'Выберите...') : selected!.label}
        </span>
        <svg className="select-input__chevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
          <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      <Floating anchorRef={triggerRef} floatingRef={popoverRef} open={open} className="floating-popover">
        {showSearch && (
          <div className="select-input__search">
            <svg className="select-input__search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
            </svg>
            <input
              ref={searchRef}
              type="text"
              className="select-input__search-field"
              value={query}
              placeholder="Поиск..."
              autoComplete="off"
              role="combobox"
              aria-expanded={open}
              aria-autocomplete="list"
              onChange={(e) => { setQuery(e.target.value); setHighlight(0); }}
              onKeyDown={onKey}
            />
          </div>
        )}
        <ul className="select-input__list" role="listbox" ref={listRef}>
          {filtered.length === 0 ? (
            <li className="select-input__empty">{query ? 'Ничего не найдено' : 'Нет вариантов'}</li>
          ) : filtered.map((opt, i) => (
            <li
              key={`${opt.value}-${i}`}
              role="option"
              aria-selected={String(opt.value) === currentValue}
              className={`select-input__item${i === highlight ? ' is-highlighted' : ''}${String(opt.value) === currentValue ? ' is-selected' : ''}`}
              onMouseEnter={() => setHighlight(i)}
              onMouseDown={(e) => { e.preventDefault(); choose(opt); }}
            >
              {opt.label}
            </li>
          ))}
        </ul>
      </Floating>
    </div>
  );
}
