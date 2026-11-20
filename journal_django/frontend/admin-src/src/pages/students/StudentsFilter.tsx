import { useState } from 'react';
import { Combobox, type Option } from '../../components/form/Combobox';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { FilterPopover } from '../../components/filters/FilterPopover';

interface Props {
  /** Применённые фильтры: ключи — имена параметров бэка (filter[...]). */
  filters: Record<string, string>;
  managerOptions: Option[];
  stageOptions: Option[];
  onApply: (next: Record<string, string>) => void;
}

/** Текстовые фильтры списка учеников: ключ — имя параметра бэка. */
const TEXT_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'full_name', label: 'Ученик' },
  { key: 'parent1_name', label: 'Родитель 1' },
  { key: 'parent1_phone', label: 'Телефон родителя 1' },
  { key: 'platform_id', label: 'Platform ID' },
];

/** Убирает пустые значения, чтобы в адрес не попадали пустые filter[x]. */
function compact(f: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(f)) if (v && v.trim() !== '') out[k] = v;
  return out;
}

/** Поиск по ученикам: лупа с панелью фильтров вместо строки фильтров над таблицей. */
export function StudentsFilter({ filters, managerOptions, stageOptions, onApply }: Props) {
  const [draft, setDraft] = useState<Record<string, string>>(filters);
  const active = Object.keys(compact(filters)).length > 0;
  const set = (key: string, value: string) => setDraft((d) => ({ ...d, [key]: value }));

  return (
    <FilterPopover
      active={active}
      label="Поиск по ученикам"
      onOpen={() => setDraft(filters)}
      onSubmit={() => onApply(compact(draft))}
      onReset={() => onApply({})}
    >
      {TEXT_FIELDS.map((f) => (
        <Field key={f.key} label={f.label}>
          <TextInput
            value={draft[f.key] ?? ''}
            onChange={(e) => set(f.key, e.target.value)}
            aria-label={f.label}
          />
        </Field>
      ))}
      <Field label="Менеджер">
        <Combobox
          value={draft.manager_id ?? ''}
          onChange={(v) => set('manager_id', v)}
          options={managerOptions}
          placeholder="Все менеджеры"
          aria-label="Менеджер"
        />
      </Field>
      <Field label="Стадия">
        <Combobox
          value={draft.stage_id ?? ''}
          onChange={(v) => set('stage_id', v)}
          options={stageOptions}
          placeholder="Все стадии"
          aria-label="Стадия"
        />
      </Field>
    </FilterPopover>
  );
}
