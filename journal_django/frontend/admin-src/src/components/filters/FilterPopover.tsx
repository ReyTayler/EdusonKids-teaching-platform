import { useState, type FormEvent, type ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Button } from '../ui/Button';

interface Props {
  /** Есть ли применённые фильтры: подсвечивает лупу и показывает «×». */
  active: boolean;
  /** Подпись панели для скринридеров. */
  label: string;
  /** Поля панели (обычно <Field>…). Раскладываются в 2 колонки, на узком экране — в одну. */
  children: ReactNode;
  /** Панель открыта: владелец копирует применённые значения в черновик. */
  onOpen: () => void;
  /** «Искать» или Enter: владелец применяет черновик. Панель закроется сама. */
  onSubmit: () => void;
  /** «×»: владелец сбрасывает все фильтры. */
  onReset: () => void;
}

export function FilterSearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

/**
 * Кнопка-лупа с выпадающей панелью фильтров: слитная группа [лупа | ×] и форма.
 * Правки в панели — черновик у владельца: применяются только по «Искать», иначе
 * список перезапрашивался бы на каждый выбор. Esc и клик вне панели — без применения.
 * Вынесено из поиска календаря, чтобы все списки с фильтрами выглядели одинаково.
 */
export function FilterPopover({ active, label, children, onOpen, onSubmit, onReset }: Props) {
  const [open, setOpen] = useState(false);

  // Черновик снимается при открытии, в обработчике: эффект сработал бы после
  // отрисовки, и панель на кадр показала бы устаревшие значения.
  const onOpenChange = (next: boolean) => {
    if (next) onOpen();
    setOpen(next);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault(); // Enter в поле не должен перезагружать страницу
    onSubmit();
    setOpen(false);
  };
  const reset = () => { onReset(); setOpen(false); };

  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      <div className="filter-pop">
        <Popover.Trigger asChild>
          <button
            type="button"
            className={`filter-pop__btn${open || active ? ' is-active' : ''}`}
            aria-label="Поиск"
            title="Поиск"
          >
            <FilterSearchIcon />
          </button>
        </Popover.Trigger>
        {active && (
          <button type="button" className="filter-pop__btn filter-pop__reset" aria-label="Сбросить" title="Сбросить" onClick={reset}>
            <span aria-hidden="true">×</span>
          </button>
        )}
      </div>
      <Popover.Portal>
        <Popover.Content
          className="filter-pop__panel"
          data-floating-popover
          side="bottom"
          align="start"
          sideOffset={10}
          collisionPadding={16}
          // Панель растёт вправо от лупы (влево она перекрывала бы сайдбар); отступ
          // не даёт стрелке уехать в скруглённый угол, пока она указывает на лупу.
          arrowPadding={12}
          aria-label={label}
          onOpenAutoFocus={(e) => {
            // Radix по умолчанию фокусирует первое поле, а Combobox на фокусе
            // раскрывает список с подсветкой «Все …» — первый же Enter молча
            // сбросил бы фильтр вместо поиска. Фокус — на саму панель.
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
          <form className="filter-pop__form" onSubmit={submit}>
            <div className="filter-pop__fields">{children}</div>
            <div className="filter-pop__actions">
              <Button type="submit" variant="primary" iconLeft={<FilterSearchIcon />}>Искать</Button>
            </div>
          </form>
          <Popover.Arrow className="filter-pop__arrow" width={16} height={8} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
