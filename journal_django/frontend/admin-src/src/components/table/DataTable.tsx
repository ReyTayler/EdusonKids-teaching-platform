import { useState, useMemo, type ReactNode } from 'react';
import { Paginator } from './Paginator';
import { SelectInput } from '../form/SelectInput';

export interface Column<T> {
  key: string;
  label: string;
  cell?: (row: T) => ReactNode;
  searchable?: boolean | ((row: T, query: string) => boolean);
  /** Если задано в server-mode — фильтр-шапка рендерит <select> вместо текста.
   *  Пустой value = «все». */
  searchOptions?: Array<{ value: string; label: string }>;
  sortable?: boolean;
  sortKey?: string;
  width?: number | string;
}

export interface ServerPaginationState {
  page: number;
  pageSize: number;
  total: number;
  sortBy: string;
  sortDir: 'asc' | 'desc';
  filters: Record<string, string>;
}

export interface ServerPaginationCallbacks {
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  onSortChange: (col: string, dir: 'asc' | 'desc') => void;
  onFiltersChange: (filters: Record<string, string>) => void;
}

interface Props<T> {
  data: T[];
  columns: Column<T>[];
  /** Подпись для ассистивных технологий. Видимый заголовок страницы рисует
   *  PageHeader — таблица им больше не владеет (см. комментарий к компоненту). */
  title?: string;
  onRowClick?: (row: T) => void;
  /** CSS-класс строки по её данным — подсветка состояния, требующего внимания
   *  (напр. `.row--danger` на «Ждёт решения»). Возвращать undefined там, где
   *  подсветки нет: пустая строка добавила бы className="" на каждый <tr>. */
  rowClassName?: (row: T) => string | undefined;
  /** Действия в тулбаре фильтров. Основные действия страницы — в PageHeader. */
  headerActions?: ReactNode;
  /** Содержимое липкой колонки действий у правого края строки (кнопка
   *  «Открыть» в списках сущностей). Колонка не участвует в настройках
   *  видимости колонок: скрыть единственный способ открыть карточку нельзя. */
  rowAction?: (row: T) => ReactNode;
  /** Просторный ритм строки (см. `.table-panel--roomy` в table.css) — списки
   *  сущностей. По умолчанию таблица плотная. */
  roomy?: boolean;
  // если передан — переключение в server-mode
  serverPagination?: ServerPaginationState & ServerPaginationCallbacks;
  isLoading?: boolean;
}

/**
 * Таблица данных.
 *
 * Раньше компонент рисовал ещё и заголовок страницы (`.section-header` с
 * title и счётчиком) — из-за чего заголовок был свойством таблицы, а не
 * страницы: две таблицы давали два заголовка уровня страницы, а страница без
 * таблицы оставалась без шапки вовсе. Теперь заголовок рисует PageHeader.
 *
 * Фильтры переехали из второго ряда `<thead>` в тулбар над таблицей: в шапке
 * они делали её двухэтажной, требовали sticky-привязки к магической высоте
 * (`top: 37px`) и сжимали поля ввода до ширины колонки.
 */
export function DataTable<T>({ data, columns, title, onRowClick, rowClassName, headerActions, rowAction, roomy, serverPagination, isLoading }: Props<T>) {
  // Колонка действий не входит в `columns`, но занимает ячейку в каждой строке —
  // пустое состояние обязано растянуться и на неё, иначе «Ничего не найдено»
  // окажется прижатым влево, а справа повиснет пустая липкая ячейка.
  const totalCols = columns.length + (rowAction ? 1 : 0);
  const panelClass = roomy ? ' table-panel--roomy' : '';
  // Курсор задаётся всегда, а не только при наличии onRowClick: глобальное
  // правило `tbody tr { cursor: pointer }` в table.css иначе обещало бы клик
  // там, где строка ничего не делает.
  const rowStyle = { cursor: onRowClick ? 'pointer' : 'default' } as const;
  const [filters, setFilters] = useState<Record<string, string>>({});
  const hasFilters = Object.values(filters).some((v) => v && v.trim() !== '');

  const filtered = useMemo(() => {
    if (serverPagination) return data; // server-mode: data уже отфильтрована/срезана
    return data.filter((row) => {
      for (const col of columns) {
        if (!col.searchable) continue;
        const q = (filters[col.key] || '').trim().toLowerCase();
        if (!q) continue;
        if (typeof col.searchable === 'function') {
          if (!col.searchable(row, q)) return false;
        } else {
          const val = (row as Record<string, unknown>)[col.key];
          if (val == null) return false;
          if (!String(val).toLowerCase().includes(q)) return false;
        }
      }
      return true;
    });
  }, [data, filters, columns, serverPagination]);

  // ── SERVER MODE ──
  if (serverPagination) {
    const sp = serverPagination;

    const handleSortClick = (col: Column<T>) => {
      if (col.sortable === false) return;
      const sortKey = col.sortKey || col.key;
      const dir =
        sp.sortBy === sortKey && sp.sortDir === 'desc' ? 'asc' : 'desc';
      sp.onSortChange(sortKey, dir);
    };

    const handleFilterChange = (colKey: string, value: string) => {
      const next = { ...sp.filters };
      if (value) next[colKey] = value;
      else delete next[colKey];
      sp.onFiltersChange(next);
    };

    const hasServerFilters = Object.values(sp.filters).some((v) => v && v.trim() !== '');

    return (
      <div className="table-block">
        <FilterBar
          columns={columns}
          values={sp.filters}
          onChange={handleFilterChange}
          onReset={() => sp.onFiltersChange({})}
          hasFilters={hasServerFilters}
          actions={headerActions}
        />
        <div className={`table-panel data-table-wrapper${panelClass}${isLoading ? ' data-table--loading' : ''}`}>
          <div className="table-wrap">
            <table className="data-table" aria-label={title}>
              <thead>
                <tr>
                  {columns.map((c) => {
                    const sortKey = c.sortKey || c.key;
                    const isSortable = c.sortable !== false;
                    const isActive = sp.sortBy === sortKey;
                    return (
                      <th
                        key={c.key}
                        style={c.width ? { width: c.width } : undefined}
                        className={isSortable ? 'sortable' : undefined}
                        onClick={isSortable ? () => handleSortClick(c) : undefined}
                      >
                        {c.label}
                        {isActive && (
                          <span className="sort-mark">{sp.sortDir === 'asc' ? '▲' : '▼'}</span>
                        )}
                      </th>
                    );
                  })}
                  {rowAction && (
                    <th className="col-actions">
                      <span className="sr-only">Действия</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {data.length === 0 ? (
                  <tr>
                    <EmptyRow cols={totalCols} filtered={Object.values(sp.filters).some((v) => v)} />
                  </tr>
                ) : data.map((row, i) => (
                  <tr
                    key={i}
                    className={rowClassName?.(row)}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    style={rowStyle}
                  >
                    {columns.map((c) => (
                      <td key={c.key}>
                        {c.cell ? c.cell(row) : String((row as Record<string, unknown>)[c.key] ?? '—')}
                      </td>
                    ))}
                    {rowAction && <td className="col-actions">{rowAction(row)}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Paginator
            page={sp.page}
            pageSize={sp.pageSize}
            total={sp.total}
            onPageChange={sp.onPageChange}
            onPageSizeChange={sp.onPageSizeChange}
          />
        </div>
      </div>
    );
  }

  // ── CLIENT MODE ──
  return (
    <div className="table-block">
      <FilterBar
        columns={columns}
        values={filters}
        onChange={(key, value) => setFilters((f) => ({ ...f, [key]: value }))}
        onReset={() => setFilters({})}
        hasFilters={hasFilters}
        actions={headerActions}
      />
      <div className={`table-panel${panelClass}`}>
        <div className="table-wrap">
          <table className="data-table" aria-label={title}>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.key} style={c.width ? { width: c.width } : undefined}>{c.label}</th>
                ))}
                {rowAction && (
                  <th className="col-actions">
                    <span className="sr-only">Действия</span>
                  </th>
                )}
              </tr>
            </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <EmptyRow cols={totalCols} filtered={hasFilters} />
              </tr>
            ) : filtered.map((row, i) => (
              <tr
                key={i}
                className={rowClassName?.(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={rowStyle}
              >
                {columns.map((c) => (
                  <td key={c.key}>
                    {c.cell ? c.cell(row) : String((row as Record<string, unknown>)[c.key] ?? '—')}
                  </td>
                ))}
                {rowAction && <td className="col-actions">{rowAction(row)}</td>}
              </tr>
            ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/**
 * Тулбар фильтров над таблицей.
 *
 * Раньше фильтры жили вторым рядом `<thead>`: шапка становилась двухэтажной,
 * поля сжимались до ширины своей колонки (в узких колонках — до нечитаемых
 * 40px), а липкое позиционирование приходилось привязывать к магической
 * высоте первого ряда (`top: 37px`), которая ехала от любой правки padding.
 *
 * Здесь у каждого фильтра своя подпись, поле имеет нормальную ширину, а сброс
 * появляется только когда есть что сбрасывать.
 */
function FilterBar<T>({ columns, values, onChange, onReset, hasFilters, actions }: {
  columns: Column<T>[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onReset: () => void;
  hasFilters: boolean;
  actions?: ReactNode;
}) {
  const filterable = columns.filter((c) => c.searchable);
  if (filterable.length === 0 && !actions) return null;

  return (
    <div className="filterbar">
      <div className="filterbar__fields">
        {/* Названия колонок — внутри контролов, а не отдельными подписями сверху:
            при пяти фильтрах подписи в верхнем регистре оказывались шире самих
            полей и ряд разваливался на два этажа. Пустой список показывает своё
            название, выбранный — значение, так что понятно, что и чем отфильтровано. */}
        {filterable.map((c) => (
          <div
            key={c.key}
            className={`filterbar__field${c.searchOptions ? '' : ' filterbar__field--text'}`}
          >
            {c.searchOptions ? (
              /* SelectInput кладёт className на ОБЁРТКУ, а видимый контрол —
                 вложенная кнопка .select-input__trigger со своими размерами.
                 Класс задаёт ширину, вид триггера подгоняет CSS. */
              <SelectInput
                className="filterbar__select"
                aria-label={c.label}
                value={values[c.key] || ''}
                onChange={(e) => onChange(c.key, e.target.value)}
                options={[{ value: '', label: `${c.label}: все` }, ...c.searchOptions]}
                placeholder={c.label}
              />
            ) : (
              <>
                <svg className="filterbar__icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <input
                  type="text"
                  className="filterbar__control filterbar__control--search"
                  aria-label={c.label}
                  placeholder={c.label}
                  value={values[c.key] || ''}
                  onChange={(e) => onChange(c.key, e.target.value)}
                />
              </>
            )}
          </div>
        ))}
      </div>
      <div className="filterbar__actions">
        {hasFilters && (
          <button type="button" className="btn-reset-filters" onClick={onReset}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
            Сбросить
          </button>
        )}
        {actions}
      </div>
    </div>
  );
}

/**
 * Пустое состояние таблицы. Было продублировано дословно в обеих ветках
 * (серверная и клиентская пагинация) — при правке текста менять пришлось бы
 * оба места. Подсказка появляется только когда фильтры реально заданы: иначе
 * совет «сбросьте фильтры» вводит в заблуждение.
 */
function EmptyRow({ cols, filtered }: { cols: number; filtered: boolean }) {
  return (
    <td colSpan={cols} className="data-table__empty">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <div>Ничего не найдено</div>
      {filtered && <div className="data-table__empty-hint">Попробуйте сбросить фильтры</div>}
    </td>
  );
}
