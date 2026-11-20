import { useState, useMemo, type ReactNode } from 'react';
import { Paginator } from './Paginator';
import { FilterToolbar, TableFilterPanel, columnFilterFields, type FilterField } from '../filters';

export interface Column<T> {
  key: string;
  label: string;
  cell?: (row: T) => ReactNode;
  searchable?: boolean | ((row: T, query: string) => boolean);
  /** Если задано в server-mode — панель поиска рендерит выбор вместо текстового поля.
   *  Пустой value = «все». */
  searchOptions?: Array<{ value: string; label: string }>;
  sortable?: boolean;
  sortKey?: string;
  width?: number | string;
  /** Переносить длинный заголовок на несколько строк (обычно заголовки в одну строку). */
  wrapHeader?: boolean;
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
  /** Стабильный ключ строки. Без него ключом служит индекс — для списков, где id
   *  уникален только вместе с типом строки (зарплата: урок и отмена), нужен свой. */
  rowKey?: (row: T) => string | number;
  /** Дополнительные поля панели поиска, не привязанные к колонкам (например,
   *  период дат). Значения лежат в тех же `filters`, что и поля колонок. */
  extraFields?: FilterField[];
  /** Действия в тулбаре фильтров. Основные действия страницы — в PageHeader. */
  headerActions?: ReactNode;
  /** Лупа вынесена страницей в PageHeader (через columnFilterFields +
   *  TableFilterPanel): встроенная панель не рисуется, строка остаётся только
   *  если есть headerActions. */
  searchOutside?: boolean;
  /** Клиентский режим: фильтры принадлежат странице (нужны ей для вынесенной
   *  лупы). Без пары filters/onFiltersChange таблица хранит их сама. */
  filters?: Record<string, string>;
  onFiltersChange?: (next: Record<string, string>) => void;
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
 * Фильтры живут в панели под лупой над таблицей (TableToolbar): поля выводятся
 * из `searchable`/`searchOptions` колонок.
 */
export function DataTable<T>({ data, columns, title, onRowClick, rowClassName, rowKey, headerActions, searchOutside, filters: controlledFilters, onFiltersChange, extraFields, rowAction, roomy, serverPagination, isLoading }: Props<T>) {
  // Колонка действий не входит в `columns`, но занимает ячейку в каждой строке —
  // пустое состояние обязано растянуться и на неё, иначе «Ничего не найдено»
  // окажется прижатым влево, а справа повиснет пустая липкая ячейка.
  const totalCols = columns.length + (rowAction ? 1 : 0);
  const panelClass = roomy ? ' table-panel--roomy' : '';
  // Курсор задаётся всегда, а не только при наличии onRowClick: глобальное
  // правило `tbody tr { cursor: pointer }` в table.css иначе обещало бы клик
  // там, где строка ничего не делает.
  const rowStyle = { cursor: onRowClick ? 'pointer' : 'default' } as const;
  const [ownFilters, setOwnFilters] = useState<Record<string, string>>({});
  const filters = controlledFilters ?? ownFilters;
  const setFilters = onFiltersChange ?? setOwnFilters;
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

    return (
      <div className="table-block">
        <TableToolbar
          columns={columns}
          filters={sp.filters}
          onApply={sp.onFiltersChange}
          actions={headerActions}
          searchOutside={searchOutside}
          extraFields={extraFields}
          title={title}
        />
        <div className={`table-panel data-table-wrapper${panelClass}${isLoading ? ' data-table--loading' : ''}`}>
          <div className="table-wrap">
            <table className="data-table" aria-label={title}>
              <thead>
                <tr>
                  {columns.map((c) => {
                    const sortKey = c.sortKey || c.key;
                    const isSortable = c.sortable !== false;
                    const isActive = isSortable && sp.sortBy === sortKey;
                    return (
                      <th
                        key={c.key}
                        style={c.width ? { width: c.width } : undefined}
                        className={[isSortable && 'sortable', c.wrapHeader && 'th--wrap'].filter(Boolean).join(' ') || undefined}
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
                    key={rowKey ? rowKey(row) : i}
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
      <TableToolbar
        columns={columns}
        filters={filters}
        onApply={setFilters}
        actions={headerActions}
        searchOutside={searchOutside}
        extraFields={extraFields}
        title={title}
      />
      <div className={`table-panel${panelClass}`}>
        <div className="table-wrap">
          <table className="data-table" aria-label={title}>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.key} className={c.wrapHeader ? 'th--wrap' : undefined} style={c.width ? { width: c.width } : undefined}>{c.label}</th>
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
                key={rowKey ? rowKey(row) : i}
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
 * Строка над таблицей: лупа с панелью фильтров и действия страницы.
 *
 * Поля панели выводятся из колонок: `searchable` — поле, `searchOptions` — выбор
 * из списка вместо текста. Раньше это была строка полей с живым вводом, из-за чего
 * серверная таблица перезапрашивалась на каждую букву; теперь фильтры применяются
 * по «Искать» (общий модуль components/filters). Без полей и действий не рисуется.
 */
function TableToolbar<T>({ columns, filters, onApply, actions, searchOutside, extraFields, title }: {
  columns: Column<T>[];
  filters: Record<string, string>;
  onApply: (next: Record<string, string>) => void;
  actions?: ReactNode;
  searchOutside?: boolean;
  extraFields?: FilterField[];
  title?: string;
}) {
  const fields = searchOutside ? [] : columnFilterFields(columns, extraFields);
  if (fields.length === 0 && !actions) return null;

  return (
    <FilterToolbar actions={actions}>
      {fields.length > 0 && (
        <TableFilterPanel
          fields={fields}
          filters={filters}
          onApply={onApply}
          label={title ? `Поиск: ${title}` : 'Поиск'}
        />
      )}
    </FilterToolbar>
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
