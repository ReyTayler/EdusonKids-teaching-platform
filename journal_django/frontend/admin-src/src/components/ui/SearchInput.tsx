import { useEffect, useRef, useState } from 'react';

interface SearchInputProps {
  /** Подтверждённое значение (то, что уже ушло на сервер). */
  value: string;
  /** Вызывается после паузы в наборе, а не на каждую букву. */
  onChange: (value: string) => void;
  placeholder?: string;
  /** Пауза перед отправкой, мс. */
  delay?: number;
  /** Ширина поля; по умолчанию — 320px из макета. */
  width?: number | string;
  autoFocus?: boolean;
}

/**
 * Поле поиска с задержкой и очисткой.
 *
 * Своё состояние держит внутри: поле обязано отзываться на каждую букву
 * мгновенно, а запрос уходит после паузы. Если поднять значение наверх и
 * ждать ответ сервера, курсор будет прыгать на медленной сети.
 *
 * Внешнее значение всё же слушается — им пользуются «очистить фильтры» и
 * возврат по ссылке с параметром в адресе.
 */
export function SearchInput({
  value,
  onChange,
  placeholder = 'Поиск',
  delay = 300,
  width = 320,
  autoFocus,
}: SearchInputProps) {
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);
  // Держим в ref, чтобы таймер не перезапускался из-за новой ссылки на
  // обработчик при каждом рендере родителя.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (draft === value) return;
    const timer = window.setTimeout(() => onChangeRef.current(draft), delay);
    return () => window.clearTimeout(timer);
  }, [draft, value, delay]);

  const clear = () => {
    setDraft('');
    onChangeRef.current('');
    inputRef.current?.focus();
  };

  return (
    <div className="ui-search" style={{ width }}>
      <SearchGlyph />
      <input
        ref={inputRef}
        className="ui-search__input"
        type="search"
        role="searchbox"
        value={draft}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          // Esc очищает поле, а не закрывает страницу — привычка из браузерных
          // строк поиска.
          if (e.key === 'Escape' && draft) {
            e.preventDefault();
            clear();
          }
        }}
      />
      {draft && (
        <button type="button" className="ui-search__clear" aria-label="Очистить поиск" onClick={clear}>
          ×
        </button>
      )}
    </div>
  );
}

function SearchGlyph() {
  return (
    <svg
      className="ui-search__icon" width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}
