import { useEffect, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { ROLE_LABELS } from '../../lib/labels';
import { type Role } from '../../lib/permissions';
import { NAV_ICONS, NAV_PINNED, NAV_GROUPS, groupKeyOfPath } from './navConfig';
import { NavGroup } from './NavGroup';
import { BrandMark } from './BrandMark';
import { ThemeToggle } from './ThemeToggle';

/** Пользователь: имя и роль, без аватара (как .who в Eduson). */
export function SidebarUser() {
  const { me } = useAuth();
  return (
    <div className="side-user">
      <span className="side-user__name">{me?.name || 'Admin'}</span>
      <span className="side-user__role">{me?.role ? ROLE_LABELS[me.role] : 'Администратор'}</span>
    </div>
  );
}

/** Кнопка «Выйти» с тем же оформлением, что у переключателя темы. */
export function LogoutButton({ rail }: { rail?: boolean }) {
  const { logout } = useAuth();
  return (
    <button
      type="button"
      className="side-btn"
      onClick={() => { void logout(); }}
      title={rail ? 'Выйти' : undefined}
      aria-label="Выйти"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
        <polyline points="16 17 21 12 16 7"/>
        <line x1="21" y1="12" x2="9" y2="12"/>
      </svg>
      <span className="side-btn__label">Выйти</span>
    </button>
  );
}

interface Props {
  /** Рельса: колонка сужена до значков, подписи прячутся, разделы группы
   *  открываются вылетающей панелью (см. NavGroup). */
  rail?: boolean;
  /** Переключатель рельсы. Без обработчика кнопка не рисуется. */
  onToggle?: () => void;
  toggleLabel?: string;
}

/** Левая колонка навигации admin SPA. */
export function Sidebar({ rail, onToggle, toggleLabel = 'Свернуть боковую панель' }: Props = {}) {
  const { me } = useAuth();
  const role = me?.role as Role | undefined;
  const { pathname } = useLocation();

  // Раскрытая группа выводится из маршрута, а не хранится между сессиями:
  // открыта группа текущего раздела; клик по другой закрывает прежнюю.
  const activeKey = groupKeyOfPath(pathname);
  const [openKey, setOpenKey] = useState<string | null>(activeKey);
  useEffect(() => {
    // Переход в раздел другой группы (по ссылке со страницы, не из колонки)
    // раскрывает нужную группу. Свёрнутая вручную остаётся свёрнутой до смены пути.
    const key = groupKeyOfPath(pathname);
    if (key) setOpenKey(key);
  }, [pathname]);

  const pinned = NAV_PINNED.filter((it) => !it.can || it.can(role));

  return (
    <aside className={`sidebar${rail ? ' sidebar--rail' : ''}`}>
      <div className="sidebar__head">
        <Link to="/admin/dashboard" className="sidebar__brand" aria-label="Eduson Kids — на дашборд">
          {/* В рельсе остаётся только квадрат знака. */}
          <BrandMark compact={rail} subtitle={rail ? undefined : 'Администрирование'} />
        </Link>
        {onToggle && (
          <button
            type="button"
            className="sidebar__toggle"
            onClick={onToggle}
            aria-label={toggleLabel}
            title={toggleLabel}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points={rail ? '9 18 15 12 9 6' : '15 18 9 12 15 6'} />
            </svg>
          </button>
        )}
      </div>
      <nav className="sidebar__nav" aria-label="Разделы">
        <div className="side-pinned">
          {pinned.map((it) => (
            <NavLink
              key={it.key}
              to={it.path}
              className={({ isActive }) => `side-top${isActive ? ' side-top--active' : ''}`}
              title={rail ? it.label : undefined}
              aria-label={rail ? it.label : undefined}
            >
              {NAV_ICONS[it.key]}
              <span className="side-top__label">{it.label}</span>
            </NavLink>
          ))}
        </div>
        {NAV_GROUPS.map((group) => {
          const items = group.items.filter((it) => !it.can || it.can(role));
          if (items.length === 0) return null;
          return (
            <NavGroup
              key={group.key}
              group={group}
              items={items}
              rail={rail}
              open={openKey === group.key}
              hasActive={activeKey === group.key}
              onToggle={() => setOpenKey((k) => (k === group.key ? null : group.key))}
            />
          );
        })}
      </nav>
      <div className="sidebar__foot">
        <SidebarUser />
        <ThemeToggle className="side-btn" showLabel />
        <LogoutButton rail={rail} />
      </div>
    </aside>
  );
}
