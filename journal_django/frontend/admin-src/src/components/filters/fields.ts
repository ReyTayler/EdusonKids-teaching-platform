/**
 * Декларативное описание поля панели поиска. Раздел перечисляет только поля —
 * вид, черновик, «Искать»/Enter и сброс обеспечивает TableFilterPanel.
 * `key` — имя параметра фильтра (для таблиц с сервером — filter[key] бэка).
 */
interface FieldBase {
  key: string;
  label: string;
}

export interface FilterOption {
  value: string;
  label: string;
  /** Приглушить пункт (архивные записи); учитывается в Combobox. */
  muted?: boolean;
}

/** Строка: «Ученик», «Телефон». `numeric` оставляет в поле только цифры. */
export interface TextFilterField extends FieldBase {
  kind: 'text';
  placeholder?: string;
  numeric?: boolean;
}

/** Короткий список (SelectInput; при длинном сам показывает поиск). */
export interface SelectFilterField extends FieldBase {
  kind: 'select';
  options: FilterOption[];
  /** Подпись пункта «все»; по умолчанию «Все». */
  allLabel?: string;
}

/** Длинный список с поиском по набору (Combobox): менеджеры, ученики, группы. */
export interface ComboboxFilterField extends FieldBase {
  kind: 'combobox';
  options: FilterOption[];
  allLabel?: string;
}

/** Галочка: в фильтре хранится `checkedValue` (по умолчанию 'true'), снята — ключа нет. */
export interface CheckboxFilterField extends FieldBase {
  kind: 'checkbox';
  checkedValue?: string;
}

/** Дата (DateInput): в фильтре хранится 'YYYY-MM-DD', пусто — ключа нет. */
export interface DateFilterField extends FieldBase {
  kind: 'date';
  placeholder?: string;
}

export type FilterField =
  | TextFilterField
  | SelectFilterField
  | ComboboxFilterField
  | CheckboxFilterField
  | DateFilterField;

/** Все параметры фильтра, которыми владеет поле. */
export function fieldKeys(f: FilterField): string[] {
  return [f.key];
}

/** Минимум от колонки таблицы, нужный для поля панели (структурно совпадает с Column из DataTable). */
interface FilterableColumn {
  key: string;
  label: string;
  searchable?: unknown;
  searchOptions?: Array<{ value: string; label: string }>;
}

/**
 * Поля панели поиска из колонок таблицы: `searchable` — текстовое поле,
 * `searchOptions` — выбор из списка. Единый источник и для встроенной строки
 * DataTable, и для страниц, которые выносят лупу в шапку (PageHeader actions):
 * набор полей в обоих местах не может разойтись.
 */
export function columnFilterFields(
  columns: FilterableColumn[],
  extraFields: FilterField[] = [],
): FilterField[] {
  return [
    ...columns
      .filter((c) => c.searchable)
      .map((c): FilterField => c.searchOptions
        ? { kind: 'select', key: c.key, label: c.label, options: c.searchOptions }
        : { kind: 'text', key: c.key, label: c.label }),
    ...extraFields,
  ];
}
