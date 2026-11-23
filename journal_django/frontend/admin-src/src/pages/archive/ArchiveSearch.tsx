import { useState } from 'react';
import { FilterPopover } from '../../components/filters/FilterPopover';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';

export function ArchiveSearch({ value, onApply }: { value: string; onApply: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  return (
    <FilterPopover active={!!value.trim()} label="Поиск по архиву"
      onOpen={() => setDraft(value)} onSubmit={() => onApply(draft.trim().replace(/\s+/g, ' '))}
      onReset={() => onApply('')}>
      <div className="archive-search-field"><Field label="Поиск по всему архиву" full help={
        <>Поиск охватывает преподавателей, группы и направления во всём архиве.
          Можно искать по имени, названию, email, телефону или ID (например, #123).
          Группы также находятся по преподавателю и направлению. После поиска можно уточнить тип записи.</>
      }>
        <TextInput aria-label="Поиск по всему архиву" placeholder="Имя, название, email или ID"
          value={draft} maxLength={200} autoComplete="off" onChange={(e) => setDraft(e.target.value)} />
      </Field></div>
    </FilterPopover>
  );
}
