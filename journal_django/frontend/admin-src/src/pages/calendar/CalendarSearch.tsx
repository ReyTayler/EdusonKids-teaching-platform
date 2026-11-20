import { useState } from 'react';
import { Combobox, type Option } from '../../components/form/Combobox';
import { Field } from '../../components/form/Field';
import { FilterPopover } from '../../components/filters/FilterPopover';

export interface CalendarFilters {
  teacherId: number | null;
  /** Название направления, NO_DIRECTION_KEY или null («все»). */
  direction: string | null;
}

interface Props {
  filters: CalendarFilters;
  teacherOptions: Option[];
  directionOptions: Option[];
  onApply: (next: CalendarFilters) => void;
}

/**
 * Поиск по календарю: панель с фильтрами «Преподаватель» и «Направление».
 * Кнопки и поповер — общий FilterPopover; здесь только черновик и поля.
 */
export function CalendarSearch({ filters, teacherOptions, directionOptions, onApply }: Props) {
  const [draft, setDraft] = useState<CalendarFilters>(filters);
  const active = filters.teacherId != null || filters.direction != null;

  return (
    <FilterPopover
      active={active}
      label="Поиск по календарю"
      onOpen={() => setDraft(filters)}
      onSubmit={() => onApply(draft)}
      onReset={() => onApply({ teacherId: null, direction: null })}
    >
      <Field label="Преподаватель">
        <Combobox
          value={draft.teacherId != null ? String(draft.teacherId) : ''}
          onChange={(v) => setDraft((d) => ({ ...d, teacherId: v ? Number(v) : null }))}
          options={teacherOptions}
          placeholder="Все преподаватели"
          aria-label="Преподаватель"
        />
      </Field>
      <Field label="Направление">
        <Combobox
          value={draft.direction ?? ''}
          onChange={(v) => setDraft((d) => ({ ...d, direction: v || null }))}
          options={directionOptions}
          placeholder="Все направления"
          aria-label="Направление"
        />
      </Field>
    </FilterPopover>
  );
}
