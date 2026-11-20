import { TableFilterPanel, type FilterField, type FilterOption } from '../../components/filters';

interface Props {
  /** Применённые фильтры: ключи — имена параметров бэка (filter[...]). */
  filters: Record<string, string>;
  managerOptions: FilterOption[];
  stageOptions: FilterOption[];
  onApply: (next: Record<string, string>) => void;
}

/** Поиск по ученикам: описание полей для общей панели (components/filters). */
export function StudentsFilter({ filters, managerOptions, stageOptions, onApply }: Props) {
  const fields: FilterField[] = [
    { kind: 'text', key: 'full_name', label: 'Ученик' },
    { kind: 'text', key: 'parent1_name', label: 'Родитель 1' },
    { kind: 'text', key: 'parent1_phone', label: 'Телефон родителя 1' },
    { kind: 'text', key: 'platform_id', label: 'Platform ID' },
    { kind: 'combobox', key: 'manager_id', label: 'Менеджер', options: managerOptions, allLabel: 'Все менеджеры' },
    { kind: 'combobox', key: 'stage_id', label: 'Стадия', options: stageOptions, allLabel: 'Все стадии' },
  ];
  return <TableFilterPanel fields={fields} filters={filters} onApply={onApply} label="Поиск по ученикам" />;
}
