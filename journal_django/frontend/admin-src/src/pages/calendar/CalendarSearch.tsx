import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { Combobox, type Option } from '../../components/form/Combobox';
import { Field } from '../../components/form/Field';

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

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

const labelOf = (opts: Option[], value: string) => opts.find((o) => o.value === value)?.label ?? value;

/**
 * Поиск по календарю: одна кнопка-лупа, по клику — окно с фильтрами «Преподаватель» и
 * «Направление». Правки в окне — черновик: на календарь они попадают только по «Поиск»,
 * иначе неделя перерисовывалась бы на каждый выбор в списке. Активные фильтры видны
 * вне окна — точкой на лупе и чипами с крестиком.
 */
export function CalendarSearch({ filters, teacherOptions, directionOptions, onApply }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<CalendarFilters>(filters);

  // Черновик начинается с применённых значений при каждом открытии (в т.ч. после
  // снятия чипа снаружи). Снимок — в обработчике, а не в эффекте: эффект
  // срабатывает после отрисовки, и окно на кадр показало бы старый черновик.
  const openSearch = () => { setDraft(filters); setOpen(true); };

  const active = filters.teacherId != null || filters.direction != null;
  const apply = () => { onApply(draft); setOpen(false); };
  // «Сбросить» сразу снимает оба фильтра и закрывает окно — отдельный шаг «сбросил, потом ищи» не нужен.
  const reset = () => { onApply({ teacherId: null, direction: null }); setOpen(false); };

  return (
    <>
      <div className="cal-search">
        <button
          type="button"
          className="cal-nav-btn cal-search__btn"
          aria-label="Поиск"
          title="Поиск"
          aria-haspopup="dialog"
          onClick={openSearch}
        >
          <SearchIcon />
          {active && <span className="cal-search__dot" aria-hidden="true" />}
        </button>
        {filters.teacherId != null && (
          <span className="cal-chip">
            <span className="cal-chip__label">{labelOf(teacherOptions, String(filters.teacherId))}</span>
            <button type="button" className="cal-chip__x" aria-label="Убрать фильтр по преподавателю" onClick={() => onApply({ ...filters, teacherId: null })}>×</button>
          </span>
        )}
        {filters.direction != null && (
          <span className="cal-chip">
            <span className="cal-chip__label">{labelOf(directionOptions, filters.direction)}</span>
            <button type="button" className="cal-chip__x" aria-label="Убрать фильтр по направлению" onClick={() => onApply({ ...filters, direction: null })}>×</button>
          </span>
        )}
      </div>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Поиск по календарю"
        footer={
          <>
            <Button variant="ghost" onClick={reset}>Сбросить</Button>
            <Button variant="primary" onClick={apply}>Поиск</Button>
          </>
        }
      >
        <div className="cal-search__form">
          <Field label="Преподаватель">
            <Combobox
              value={draft.teacherId != null ? String(draft.teacherId) : ''}
              onChange={(v) => setDraft((d) => ({ ...d, teacherId: v ? Number(v) : null }))}
              options={teacherOptions}
              placeholder="Все преподаватели"
            />
          </Field>
          <Field label="Направление">
            <Combobox
              value={draft.direction ?? ''}
              onChange={(v) => setDraft((d) => ({ ...d, direction: v || null }))}
              options={directionOptions}
              placeholder="Все направления"
            />
          </Field>
        </div>
      </Dialog>
    </>
  );
}
