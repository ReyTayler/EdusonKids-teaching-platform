import { useState, type ReactNode } from 'react';
import { Avatar } from '../Avatar';
import { fmtDayDivider, fmtTimeMSK, isoDateMSK } from '../../lib/format';

/**
 * Комментарии и история в выезжающих панелях (задачи, сделки).
 *
 * Оформление у обоих разделов одно, поэтому живёт здесь, а не копией в каждой
 * панели: вторая копия разметки неизбежно разъехалась бы с первой. Ровно так же
 * поступили со строкой свойства (components/form/InlineField.tsx).
 *
 * История — лента фраз на естественном языке: аватар автора, одна связная
 * фраза, приглушённое время справа. Саму фразу собирает вызывающий раздел: она
 * зависит от его справочников (стадии, исполнители, типы), и знать про них
 * общий компонент не должен.
 *
 * Комментарии — переписка: разделитель дня, пузыри с именем автора и текстом.
 */

/** Шеврон сворачиваемой секции. Тот же рисунок, что у карточки сущности. */
function SectionChevron() {
  return (
    <svg
      className="drawer-section__chevron"
      width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

/**
 * Секция панели со сворачиваемым заголовком. По умолчанию раскрыта: и историю,
 * и переписку в карточке обычно читают сразу, сворачивают их разово — чтобы
 * добраться до свойств на узкой панели.
 *
 * Отдельного компонента для этого в проекте не было: EntityCard в
 * components/detail/DetailShell.tsx тоже сворачивается, но он про таблицу
 * «поле — значение» и живёт своим состоянием в localStorage.
 */
export function CollapsibleSection({ title, defaultOpen = true, children }: {
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`drawer-section${open ? '' : ' is-collapsed'}`}>
      <button
        type="button"
        className="drawer-section__head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <SectionChevron />
        <span className="drawer-section__title">{title}</span>
      </button>
      {open && <div className="drawer-section__body">{children}</div>}
    </div>
  );
}

/** Лента истории — контейнер строк ActivityRow. */
export function ActivityLog({ children }: { children: ReactNode }) {
  return <ul className="drawer-activity">{children}</ul>;
}

/**
 * Строка ленты: аватар автора, фраза, время справа.
 *
 * Имя автора рисуем здесь, а глагольную часть фразы («изменил(-а) тип задачи
 * на Действие») передают детьми — иначе каждому разделу пришлось бы повторять
 * разметку имени. Глаголы везде в скобочной форме: пола сотрудника в учётке
 * нет, а угадывать его по имени нельзя.
 */
export function ActivityRow({ authorName, time, children }: {
  authorName: string | null;
  /** Готовая подпись времени — форматирует вызывающий (обычно fmtRelativeDateTime). */
  time: string;
  children: ReactNode;
}) {
  // Автора у системных записей может не быть (учётку удалили) — «Система»
  // читается лучше, чем прочерк посреди фразы.
  const name = authorName || 'Система';
  return (
    <li className="drawer-activity__row">
      <Avatar name={name} size={20} />
      <span className="drawer-activity__phrase">
        <span className="drawer-activity__author">{name}</span>{' '}{children}
      </span>
      <span className="drawer-activity__time">{time}</span>
    </li>
  );
}

/** Пустая лента — своим элементом списка, чтобы <ul> не оставался пустым. */
export function ActivityEmpty({ children }: { children: ReactNode }) {
  return <li className="drawer-activity__empty">{children}</li>;
}

export interface DrawerCommentItem {
  id: number;
  author: string | null;
  /** Сырой ISO с бэка: день и время считает сам компонент — см. ниже. */
  iso: string;
  text: string | null;
}

/**
 * Переписка: комментарии пузырями, разделитель дня между пачками.
 *
 * Группировку по дням делаем здесь, а не в разделах. Во-первых, иначе оба
 * раздела повторяли бы один и тот же код; во-вторых, граница суток — вопрос
 * отображения: считается она по МСК (isoDateMSK), как и всё остальное в
 * интерфейсе, и разделам про это знать незачем.
 *
 * Список ожидается в хронологическом порядке (старые сверху) — так его и
 * читают сверху вниз.
 */
export function CommentThread({ items, emptyText }: {
  items: DrawerCommentItem[];
  emptyText: string;
}) {
  if (items.length === 0) {
    return <div className="drawer-comments__empty">{emptyText}</div>;
  }

  const days: { key: string; items: DrawerCommentItem[] }[] = [];
  for (const item of items) {
    const key = isoDateMSK(item.iso);
    const last = days[days.length - 1];
    if (last && last.key === key) last.items.push(item);
    else days.push({ key, items: [item] });
  }

  return (
    <div className="drawer-comments">
      {days.map((day) => (
        <div key={day.key || day.items[0].id} className="drawer-comments__day">
          <div className="drawer-comments__divider">
            {fmtDayDivider(day.items[0].iso)}
          </div>
          <ul className="drawer-comments__list">
            {day.items.map((c) => (
              <li key={c.id} className="drawer-comments__item">
                <Avatar name={c.author || '—'} size={24} />
                <div className="drawer-comments__bubble">
                  <div className="drawer-comments__author">{c.author || '—'}</div>
                  <div className="drawer-comments__text">{c.text}</div>
                </div>
                <span className="drawer-comments__time">{fmtTimeMSK(c.iso)}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
