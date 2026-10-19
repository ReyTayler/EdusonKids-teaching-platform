import { useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';

export interface DetailField<T> {
  key: string;
  label: string;
  cell?: (row: T) => ReactNode;
}

interface EntityCardProps<T> {
  title?: string;
  row: T;
  fields: DetailField<T>[];
}

/**
 * Карточка полей сущности (collapsible, состояние — в localStorage, общее для всех сущностей).
 * Вынесена отдельно от DetailShell, чтобы её можно было разместить внутри таба
 * (например, «Обзор» на странице группы), а не только в фиксированном месте макета.
 */
export function EntityCard<T>({ title = 'Данные', row, fields }: EntityCardProps<T>) {
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem('admin-entity-card-collapsed') === '1'
  );

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem('admin-entity-card-collapsed', next ? '1' : '0');
  };

  return (
    <div className={`entity-card${collapsed ? ' is-collapsed' : ''}`}>
      <button
        type="button"
        className="entity-card__head"
        onClick={toggle}
        aria-expanded={!collapsed}
      >
        <span className="entity-card__title">{title}</span>
        <svg className="entity-card__chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      <div className="entity-card__body">
        {fields.map((f) => (
          <div key={f.key} className="entity-card__row">
            <div className="entity-card__label">{f.label}</div>
            <div className="entity-card__value">
              {f.cell ? f.cell(row) : String((row as Record<string, unknown>)[f.key] ?? '—')}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface Props<T> {
  title: string;
  subtitle?: string;
  row: T;
  fields: DetailField<T>[];
  cardTitle?: string;
  customHero?: ReactNode;
  onEdit?: () => void;
  onDelete?: () => Promise<void>;
  deleteLabel?: string;
  /** Сущность в архиве (active=false). Тогда вместо «Архивировать» показываем
   *  «Разархивировать» (onRestore) — архивировать уже архивную нелогично. */
  archived?: boolean;
  onRestore?: () => Promise<void>;
  restoreLabel?: string;
  backTo?: string;
  /** Подпись родительского раздела в крошках («Ученики», «Группы»). */
  parentLabel?: string;
  /** Скрыть карточку полей (entity-card) — например, когда она вынесена в отдельный таб. */
  hideCard?: boolean;
  children?: ReactNode;
}

export function DetailShell<T>({
  title,
  subtitle,
  row,
  fields,
  cardTitle = 'Данные',
  customHero,
  onEdit,
  onDelete,
  deleteLabel = 'Архивировать',
  archived = false,
  onRestore,
  restoreLabel = 'Разархивировать',
  backTo,
  parentLabel,
  hideCard,
  children,
}: Props<T>) {
  const navigate = useNavigate();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const handleDelete = async () => {
    if (!confirmingDelete) { setConfirmingDelete(true); return; }
    await onDelete?.();
  };

  return (
    <div className="detail-page">
      {/* Крошки вместо голого «← Назад»: кнопка возвращала на шаг, но не
          отвечала на вопрос «где я» — на карточке ученика, открытой из группы,
          раздел был неочевиден. Стрелка «назад» осталась первым элементом. */}
      <nav className="crumbs detail-crumbs" aria-label="Хлебные крошки">
        <button
          type="button"
          className="crumbs__back"
          onClick={() => backTo ? navigate(backTo) : navigate(-1)}
          aria-label="Назад"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        {backTo && (
          <span className="crumbs__item">
            <Link to={backTo} className="crumbs__link">{parentLabel ?? 'Назад'}</Link>
            <span className="crumbs__sep" aria-hidden="true">›</span>
          </span>
        )}
        <span className="crumbs__item"><span aria-current="page">{title}</span></span>
      </nav>

      {customHero ? customHero : (
        <div className="detail-head">
          <div className="detail-head-info">
            <div className="detail-title"><h2>{title}</h2></div>
            {subtitle && <div className="detail-sub">{subtitle}</div>}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            {onEdit && (
              <button type="button" className="edit-btn" onClick={onEdit}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
                Редактировать
              </button>
            )}
            {/* Архивная сущность → «Разархивировать» (восстановление),
                иначе → «Архивировать» (с подтверждением). */}
            {archived ? (
              onRestore && (
                <button
                  type="button"
                  className="restore-btn"
                  onClick={() => { void onRestore(); }}
                >
                  {restoreLabel}
                </button>
              )
            ) : (
              onDelete && (
                <button
                  type="button"
                  className={`delete-btn${confirmingDelete ? ' is-confirming' : ''}`}
                  onClick={() => { void handleDelete(); }}
                >
                  {confirmingDelete ? 'Точно?' : deleteLabel}
                </button>
              )
            )}
          </div>
        </div>
      )}

      {!hideCard && <EntityCard title={cardTitle} row={row} fields={fields} />}

      {children}
    </div>
  );
}
