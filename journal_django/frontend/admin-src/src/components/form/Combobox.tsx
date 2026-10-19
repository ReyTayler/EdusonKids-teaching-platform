import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Floating } from './Floating';

export interface Option {
  value: string;
  label: string;
  /**
   * Вторая строка пункта: ник, пометка «занят такой-то». Участвует в поиске —
   * ник человек помнит чаще, чем полное имя из профиля Telegram.
   */
  hint?: string;
  /**
   * Пункт приглушён, но выбираем. Именно приглушён, а не disabled: занятый
   * аккаунт законно перепривязать (человек сменил преподавателя), конфликт
   * разрешает бэкенд, а не форма.
   */
  muted?: boolean;
}

interface Props {
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  placeholder?: string;
  /**
   * Значение, которое поле показывает как ПОДСКАЗКУ, а не как выбранный
   * вариант: поле остаётся пустым, и виден `placeholder` — приглушённое
   * «Назначить…» вместо обычного «— не назначен —». Сам вариант из `options`
   * не исчезает, иначе поле стало бы неочищаемым.
   *
   * Не задан (по умолчанию) — поведение прежнее: показываем подпись варианта.
   */
  placeholderValue?: string;
  /** Сколько строк помещается в выпадашке. Остальное прокручивается. По умолчанию 10. */
  maxVisible?: number;
  /**
   * Высота строки в пикселях — из неё считается maxHeight выпадашки.
   * Двухстрочные пункты (с `hint`) выше: передавать 52, иначе список
   * обрезается на середине пункта.
   */
  itemHeight?: number;
  /**
   * Доступное имя поля. Нужно, когда Combobox стоит БЕЗ обёртки `<Field>`
   * (та даёт `<label>`): иначе у input[role=combobox] нет имени вовсе, и
   * скринридер читает пустое поле — только placeholder, который исчезает
   * при вводе.
   */
  'aria-label'?: string;
}

export function Combobox({
  value, onChange, options, placeholder, placeholderValue, maxVisible = 10, itemHeight = 36,
  'aria-label': ariaLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(() => options.find((o) => o.value === value), [options, value]);

  const filtered = useMemo(() => {
    if (!open || !query.trim()) return options;
    const q = query.toLowerCase();
    return options.filter(
      (o) => o.label.toLowerCase().includes(q) || (o.hint || '').toLowerCase().includes(q),
    );
  }, [options, query, open]);

  // Click outside → close.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const t = e.target as Node;
      // Список рендерится в портале (вне containerRef) — исключаем и его.
      if (!containerRef.current?.contains(t) && !popoverRef.current?.contains(t)) {
        setOpen(false);
        setQuery('');
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  // Скролл подсвеченного пункта в зону видимости (для клавиатурной навигации).
  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.children[highlight] as HTMLElement | undefined;
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [highlight, open]);

  const choose = (opt: Option) => {
    onChange(opt.value);
    setOpen(false);
    setQuery('');
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setHighlight((h) => Math.min(filtered.length - 1, h + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === 'Enter') {
      if (open && filtered[highlight]) { e.preventDefault(); choose(filtered[highlight]); }
    } else if (e.key === 'Escape') {
      if (open) e.stopPropagation(); // закрываем список, не саму модалку
      setOpen(false);
      setQuery('');
    }
  };

  // displayValue: при открытом списке показываем query (видимый ввод поиска),
  // при закрытом — label выбранного. Значение, объявленное «пустым» через
  // placeholderValue, оставляет поле пустым — вместо подписи варианта виден
  // приглушённый placeholder.
  const displayValue = open
    ? query
    : (value === placeholderValue ? '' : (selected?.label || ''));

  return (
    <div className="combobox" ref={containerRef}>
      <input
        ref={inputRef}
        type="text"
        className="combobox__input"
        value={displayValue}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setHighlight(0); }}
        onFocus={() => { setOpen(true); setQuery(''); setHighlight(0); }}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-autocomplete="list"
      />
      <Floating
        anchorRef={inputRef}
        floatingRef={popoverRef}
        open={open}
        className="floating-popover"
        maxHeight={maxVisible * itemHeight + 8}
      >
        <ul ref={listRef} className="combobox__list" role="listbox">
          {filtered.length === 0 ? (
            <li className="combobox__empty">Ничего не найдено</li>
          ) : (
            filtered.map((opt, i) => (
              <li
                key={opt.value}
                role="option"
                aria-selected={opt.value === value}
                className={
                  `combobox__item${i === highlight ? ' is-highlighted' : ''}` +
                  `${opt.value === value ? ' is-selected' : ''}` +
                  `${opt.muted ? ' is-muted' : ''}`
                }
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => { e.preventDefault(); choose(opt); }}
              >
                <span className="combobox__item-label">{opt.label}</span>
                {opt.hint && <span className="combobox__item-hint">{opt.hint}</span>}
              </li>
            ))
          )}
        </ul>
      </Floating>
    </div>
  );
}
