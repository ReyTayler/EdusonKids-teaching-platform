import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@shared/hooks/useAuth';
import { BrandMark } from '@shared/components/shell/BrandMark';
import { ThemeToggle } from '@shared/components/shell/ThemeToggle';
import { Dialog } from '@shared/components/ui/Dialog';

/** Разделы кабинета. Вкладки — только текстом, без иконок (язык Eduson). */
const SECTIONS = [
  { key: 'calendar', label: 'Календарь', path: '/calendar' },
  { key: 'groups', label: 'Мои группы', path: '/groups' },
  { key: 'lessons', label: 'Мои уроки', path: '/lessons' },
  { key: 'report', label: 'Отчёт по уроку', path: '/report' },
  { key: 'payroll', label: 'Зарплата', path: '/payroll' },
  { key: 'knowledge', label: 'Wiki', path: '/knowledge' },
];

/** Имя и роль (как .who в Eduson): без цветного аватара. */
function Who() {
  const { me } = useAuth();
  return (
    <div className="topnav__who">
      <span className="topnav__who-name">{me?.name || 'Преподаватель'}</span>
      <span className="topnav__who-role">Преподаватель</span>
    </div>
  );
}

/**
 * Верхняя шапка кабинета преподавателя — тот же язык, что была верхняя шапка admin:
 * липкая, полупрозрачная с размытием, знак слева, вкладки в строку, справа
 * тема, имя с ролью и «Выйти». Активная вкладка — чёрная заливка.
 *
 * Узкая ширина: вкладки прячутся CSS-ом, вместо них кнопка меню; список
 * разделов открывается в общем Dialog (фокус-ловушка и Esc — оттуда).
 */
export function TeacherShell() {
  const { logout } = useAuth();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  // Переход по ссылке из меню закрывает его.
  useEffect(() => { setMenuOpen(false); }, [pathname]);

  return (
    <div className="t-shell">
      <header className="topnav">
        <div className="topnav__inner">
          <Link to="/calendar" className="topnav__brand" aria-label="Eduson Kids — кабинет преподавателя">
            <BrandMark subtitle="Кабинет преподавателя" />
          </Link>

          <nav className="topnav__tabs" aria-label="Разделы">
            {SECTIONS.map((s) => (
              <NavLink
                key={s.key}
                to={s.path}
                className={({ isActive }) => `topnav__tab${isActive ? ' topnav__tab--active' : ''}`}
              >
                {s.label}
              </NavLink>
            ))}
          </nav>

          <div className="topnav__spacer" />

          <ThemeToggle />
          <Who />
          <button type="button" className="topnav__btn" onClick={() => { void logout(); }}>
            Выйти
          </button>

          <button
            type="button"
            className="topnav__icon-btn topnav__burger"
            onClick={() => setMenuOpen(true)}
            aria-label="Открыть меню разделов"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
        </div>
      </header>

      <Dialog open={menuOpen} onOpenChange={setMenuOpen} title="Разделы">
        <div className="topnav-drawer">
          <div className="topnav-drawer__group">
            {SECTIONS.map((s) => (
              <NavLink
                key={s.key}
                to={s.path}
                onClick={() => setMenuOpen(false)}
                className={({ isActive }) => `topnav-drawer__link${isActive ? ' topnav-drawer__link--active' : ''}`}
              >
                {s.label}
              </NavLink>
            ))}
          </div>
          <div className="topnav-drawer__footer">
            <Who />
            <ThemeToggle />
            <button type="button" className="topnav__btn" onClick={() => { void logout(); }}>
              Выйти
            </button>
          </div>
        </div>
      </Dialog>

      <main className="t-main">
        <Outlet />
      </main>
    </div>
  );
}
