import { type ReactNode } from 'react';
import { Link } from 'react-router-dom';

export interface Crumb {
  label: string;
  to?: string;
}

interface Props {
  title: string;
  /** Счётчик рядом с заголовком: «Ученики · 342». */
  count?: number | string;
  /** Путь до текущей страницы. Последний элемент — сама страница, без ссылки. */
  crumbs?: Crumb[];
  /** Кнопки справа. Основное действие — первым. */
  actions?: ReactNode;
  /** Пояснение или мета: по паттерну Eduson стоит справа от заголовка, серым. */
  sub?: ReactNode;
  /**
   * Плотный режим: заголовок на ступень меньше и сжатый нижний отступ.
   * Для рабочих экранов, где на счету каждая строка данных, — канбан продлений.
   * Обычные разделы (списки, карточки сущностей) остаются на общем ритме.
   */
  dense?: boolean;
}

/**
 * Шапка страницы — одна на весь раздел (паттерн Eduson: заголовок слева,
 * пояснение и действия справа).
 *
 * Заголовок принадлежит странице, а не таблице: две таблицы на странице не
 * дают два заголовка уровня страницы, а страница без таблицы не остаётся без
 * шапки.
 *
 * Шапка не липкая: липкая полоса над рабочей таблицей отнимала бы у неё высоту.
 * Липкие элементы страниц (панель редактора, колонка статьи) отсчитывают верх
 * от --header-h: на десктопе он 0, на узком экране — высота TopBar.
 */
export function PageHeader({ title, count, crumbs, actions, sub, dense }: Props) {
  return (
    <header className={`page-header${dense ? ' page-header--dense' : ''}`}>
      <div className="page-header__inner">
        <div className="page-header__main">
          {!!crumbs?.length && (
            <nav className="crumbs" aria-label="Навигация по разделу">
              {crumbs.map((c, i) => {
                const last = i === crumbs.length - 1;
                return (
                  <span key={`${c.label}-${i}`} className="crumbs__item">
                    {c.to && !last
                      ? <Link to={c.to} className="crumbs__link">{c.label}</Link>
                      : <span aria-current={last ? 'page' : undefined}>{c.label}</span>}
                    {!last && <span className="crumbs__sep" aria-hidden="true">›</span>}
                  </span>
                );
              })}
            </nav>
          )}
          <div className="page-header__title-row">
            <h1 className="page-header__title">{title}</h1>
            {count != null && <span className="page-header__count">{count}</span>}
          </div>
        </div>
        {sub && <div className="page-header__sub">{sub}</div>}
        {actions && <div className="page-header__actions">{actions}</div>}
      </div>
    </header>
  );
}
