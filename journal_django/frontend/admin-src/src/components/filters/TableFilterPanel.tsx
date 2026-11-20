import { useState } from 'react';
import { Checkbox } from '../form/Checkbox';
import { Combobox } from '../form/Combobox';
import { DateInput } from '../form/DateInput';
import { Field } from '../form/Field';
import { SelectInput } from '../form/SelectInput';
import { TextInput } from '../form/TextInput';
import { FilterPopover } from './FilterPopover';
import { fieldKeys, type FilterField } from './fields';

interface Props {
  fields: FilterField[];
  /** Применённые фильтры: ключи — имена параметров (лишние ключи не трогаются). */
  filters: Record<string, string>;
  /** Новый полный набор фильтров (чужие ключи сохранены, пустые убраны). */
  onApply: (next: Record<string, string>) => void;
  /** Подпись панели для скринридеров. */
  label?: string;
}

/** Убирает пустые значения, чтобы в адрес не попадали пустые параметры. */
function compact(f: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(f)) if (v && v.trim() !== '') out[k] = v;
  return out;
}

/**
 * Панель поиска по описанию полей: лупа, выпадающая панель, «Искать».
 * Владеет только ключами своих полей: остальные параметры страницы
 * (например, вспомогательные f.*) при «Искать» и «×» остаются как были.
 */
export function TableFilterPanel({ fields, filters, onApply, label = 'Поиск' }: Props) {
  const [draft, setDraft] = useState<Record<string, string>>(filters);
  const own = new Set(fields.flatMap(fieldKeys));
  const active = Object.entries(filters).some(([k, v]) => own.has(k) && v && v.trim() !== '');

  const set = (key: string, value: string) => setDraft((d) => ({ ...d, [key]: value }));
  // Чужие ключи берём из применённых фильтров, свои — из черновика.
  const keepForeign = () => Object.fromEntries(Object.entries(filters).filter(([k]) => !own.has(k)));
  const apply = () => {
    const mine = Object.fromEntries(Object.entries(draft).filter(([k]) => own.has(k)));
    onApply({ ...keepForeign(), ...compact(mine) });
  };

  return (
    <FilterPopover
      active={active}
      label={label}
      onOpen={() => setDraft(filters)}
      onSubmit={apply}
      onReset={() => onApply(keepForeign())}
    >
      {fields.map((f) => renderField(f, draft, set))}
    </FilterPopover>
  );
}

function renderField(f: FilterField, draft: Record<string, string>, set: (k: string, v: string) => void) {
  switch (f.kind) {
    case 'text':
      return (
        <Field key={f.key} label={f.label}>
          <TextInput
            value={draft[f.key] ?? ''}
            placeholder={f.placeholder}
            inputMode={f.numeric ? 'numeric' : undefined}
            onChange={(e) => set(f.key, f.numeric ? e.target.value.replace(/\D/g, '') : e.target.value)}
            aria-label={f.label}
          />
        </Field>
      );
    case 'select':
      return (
        <Field key={f.key} label={f.label}>
          <SelectInput
            aria-label={f.label}
            value={draft[f.key] ?? ''}
            onChange={(e) => set(f.key, e.target.value)}
            options={[{ value: '', label: f.allLabel ?? 'Все' }, ...f.options]}
          />
        </Field>
      );
    case 'combobox':
      return (
        <Field key={f.key} label={f.label}>
          <Combobox
            value={draft[f.key] ?? ''}
            onChange={(v) => set(f.key, v)}
            // Пункт «все» — единственный способ снять выбор поля по отдельности;
            // placeholderValue показывает его приглушённой подсказкой, не значением.
            options={[{ value: '', label: f.allLabel ?? 'Все' }, ...f.options]}
            placeholder={f.allLabel ?? 'Все'}
            placeholderValue=""
            aria-label={f.label}
          />
        </Field>
      );
    case 'date':
      return (
        <Field key={f.key} label={f.label}>
          <DateInput
            value={draft[f.key] ?? ''}
            onChange={(e) => set(f.key, e.target.value)}
            placeholder={f.placeholder ?? f.label}
            aria-label={f.label}
          />
        </Field>
      );
    case 'checkbox': {
      const on = f.checkedValue ?? 'true';
      return (
        <div key={f.key} className="filter-pop__check">
          <Checkbox label={f.label} checked={draft[f.key] === on} onChange={(e) => set(f.key, e.target.checked ? on : '')} />
        </div>
      );
    }
  }
}
