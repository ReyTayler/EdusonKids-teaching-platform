import { useState, type FormEvent } from 'react';
import * as Popover from '@radix-ui/react-popover';
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

/**
 * Поиск по календарю: слитная группа [лупа | ×] и выпадающая панель с фильтрами
 * «Преподаватель» и «Направление». Правки в панели — черновик: на календарь они
 * попадают только по «Искать», иначе неделя перерисовывалась бы на каждый выбор.
 * Активные фильтры видны вне панели по заливке лупы и по кнопке «×».
 */
export function CalendarSearch({ filters, teacherOptions, directionOptions, onApply }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<CalendarFilters>(filters);

  const active = filters.teacherId != null || filters.direction != null;

  // Черновик снимается при открытии, в обработчике: эффект сработал бы после
  // отрисовки, и панель на кадр показала бы устаревшие значения.
  const onOpenChange = (next: boolean) => {
    if (next) setDraft(filters);
    setOpen(next);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault(); // Enter в поле не должен перезагружать страницу
    onApply(draft);
    setOpen(false);
  };
  const reset = () => { onApply({ teacherId: null, direction: null }); setOpen(false); };

  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      <div className="cal-search">
        <Popover.Trigger asChild>
          <button
            type="button"
            className={`cal-search__btn${open || active ? ' is-active' : ''}`}
            aria-label="Поиск"
            title="Поиск"
          >
            <SearchIcon />
          </button>
        </Popover.Trigger>
        {active && (
          <button type="button" className="cal-search__btn cal-search__reset" aria-label="Сбросить" title="Сбросить" onClick={reset}>
            <span aria-hidden="true">×</span>
          </button>
        )}
      </div>
      <Popover.Portal>
        <Popover.Content
          className="cal-search__panel"
          data-floating-popover
          align="end"
          sideOffset={10}
          collisionPadding={16}
          aria-label="Поиск по календарю"
          onOpenAutoFocus={(e) => {
            // Radix по умолчанию фокусирует первое поле, а Combobox на фокусе
            // раскрывает список с подсветкой «Все преподаватели» — первый же Enter
            // молча сбросил бы фильтр вместо поиска. Фокус — на саму панель.
            e.preventDefault();
            (e.currentTarget as HTMLElement).focus();
          }}
          onInteractOutside={(e) => {
            // Список Combobox рендерится порталом в body — формально «снаружи»
            // поповера; без этой проверки клик по варианту закрывал бы панель.
            const t = e.target as HTMLElement | null;
            if (t?.closest('[data-floating-popover]')) e.preventDefault();
          }}
        >
          <form className="cal-search__form" onSubmit={submit}>
            <div className="cal-search__fields">
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
            </div>
            <div className="cal-search__actions">
              <Button type="submit" variant="primary" iconLeft={<SearchIcon />}>Искать</Button>
            </div>
          </form>
          <Popover.Arrow className="cal-search__arrow" width={16} height={8} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
