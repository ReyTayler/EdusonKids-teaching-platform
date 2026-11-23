import { useListSearchParams } from '../../../hooks/useListSearchParams';
import { TableFilterPanel, type FilterField } from '../../../components/filters';

// Отдельный файл, а не часть RegistryTab: панель живёт в шапке дашборда и
// грузится сразу, а вкладка — ленивым чанком. Поле то же, что searchable-колонка
// «Ученик» в RegistryTable (ключ student_name уходит в `search` запроса).
const FIELDS: FilterField[] = [{ kind: 'text', key: 'student_name', label: 'Ученик' }];

/** Лупа реестра для шапки дашборда: состояние — в URL (f.student_name), как у вкладки. */
export function RegistrySearch() {
  const { filters, setFilters } = useListSearchParams({ sortBy: 'urgency', sortDir: 'asc', pageSize: 30 });
  return <TableFilterPanel fields={FIELDS} filters={filters} onApply={setFilters} label="Поиск: Реестр" />;
}
