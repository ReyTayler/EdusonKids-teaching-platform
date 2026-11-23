import { useEffect, useState, type ReactElement } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import * as RadixDialog from '@radix-ui/react-dialog';
import { useAuth } from '@shared/hooks/useAuth';
import { BrandMark } from '@shared/components/shell/BrandMark';
import { ThemeToggle } from '@shared/components/shell/ThemeToggle';

const NAV_ICONS: Record<string, ReactElement> = {
  calendar: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  ),
  schedule: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9.5" />
      <path d="M12 7v5l3 2" />
    </svg>
  ),
  groups: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  ),
  lessons: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
  ),
  payroll: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
      <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
      <path d="M18 12a2 2 0 0 0 0 4h4v-4z" />
    </svg>
  ),
  // «i» в круге — знак справки. Книга не годилась: точно такая же стоит рядом
  // на «Мои уроки», и в одной строке меню они не различались бы.
  knowledge: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9.5" />
      <line x1="12" y1="11" x2="12" y2="16.5" />
      {/* Точка над «i» — путь нулевой длины с круглым концом (см. Sidebar.tsx). */}
      <path d="M12 7.5h.01" />
    </svg>
  ),
};

const SECTIONS = [
  { key: 'calendar', label: 'Календарь', path: '/calendar' },
  { key: 'schedule', label: 'График работы', path: '/schedule' },
  { key: 'groups', label: 'Мои группы', path: '/groups' },
  { key: 'lessons', label: 'Мои уроки', path: '/lessons' },
  { key: 'payroll', label: 'Зарплата', path: '/payroll' },
  { key: 'knowledge', label: 'Wiki', path: '/knowledge' },
];


const RAIL_KEY = 'teacher:nav-rail';
const NARROW_QUERY = '(max-width: 1179px)';

function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW_QUERY).matches);
  useEffect(() => {
    const query = window.matchMedia(NARROW_QUERY);
    const update = () => setNarrow(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return narrow;
}

function MenuIcon({ close = false }: { close?: boolean }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {close ? <path d="m6 6 12 12M6 18 18 6" /> : <path d="M3 6h18M3 12h18M3 18h18" />}
  </svg>;
}

function TeacherSidebar({ rail = false, drawer = false, onToggle, onNavigate }: {
  rail?: boolean; drawer?: boolean; onToggle: () => void; onNavigate?: () => void;
}) {
  const { me, logout } = useAuth();
  const toggleLabel = drawer ? 'Закрыть меню' : rail ? 'Развернуть боковую панель' : 'Свернуть боковую панель';
  return (
    <aside className={`t-sidebar${rail ? ' t-sidebar--rail' : ''}`} aria-label="Панель преподавателя">
      <div className="t-sidebar-head">
        <NavLink to="/calendar" className="t-brand" aria-label="Кабинет преподавателя — календарь" onClick={onNavigate}>
          <BrandMark compact={rail} subtitle="Кабинет преподавателя" />
        </NavLink>
        <button type="button" className="t-icon-btn" onClick={onToggle} aria-label={toggleLabel} title={toggleLabel}>
          {drawer ? <MenuIcon close /> : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d={rail ? 'm9 6 6 6-6 6' : 'm15 6-6 6 6 6'} /></svg>}
        </button>
      </div>
      <nav className="t-sidebar-nav" aria-label="Разделы кабинета">
        {SECTIONS.map((section) => (
          <NavLink key={section.key} to={section.path} onClick={onNavigate}
            title={rail ? section.label : undefined} aria-label={section.label}
            className={({ isActive }) => `t-nav-link${isActive ? ' active' : ''}`}>
            {NAV_ICONS[section.key]}<span className="t-nav-label">{section.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="t-sidebar-footer">
        <div className="t-user" title={me?.name || 'Преподаватель'}><div className="t-user-meta"><div className="t-user-name">{me?.name || 'Преподаватель'}</div><div className="t-user-role">Преподаватель</div></div></div>
        <div className="t-sidebar-actions">
          <ThemeToggle className="t-icon-btn" />
          <button type="button" className="t-nav-link t-logout" onClick={() => { void logout(); }} aria-label="Выйти" title="Выйти">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></svg>
            <span className="t-nav-label">Выйти</span>
          </button>
        </div>
      </div>
    </aside>
  );
}

export function TeacherShell() {
  const narrow = useNarrow();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [rail, setRail] = useState(() => {
    try { return localStorage.getItem(RAIL_KEY) === '1'; } catch { return false; }
  });
  useEffect(() => { setMenuOpen(false); }, [pathname, narrow]);
  const toggleRail = () => setRail((current) => {
    const next = !current;
    try { localStorage.setItem(RAIL_KEY, next ? '1' : '0'); } catch { /* Storage unavailable. */ }
    return next;
  });
  return (
    <div className={`t-shell${narrow ? ' t-shell--narrow' : ''}`}>
      {!narrow && <TeacherSidebar rail={rail} onToggle={toggleRail} />}
      <div className="t-content">
        {narrow && <header className="t-mobile-bar">
          <RadixDialog.Root open={menuOpen} onOpenChange={setMenuOpen}>
            <RadixDialog.Trigger asChild>
              <button type="button" className="t-icon-btn" aria-label="Открыть меню разделов"><MenuIcon /></button>
            </RadixDialog.Trigger>
            <RadixDialog.Portal>
              <RadixDialog.Overlay className="t-drawer-overlay" />
              <RadixDialog.Content className="t-drawer" aria-describedby={undefined}>
                <RadixDialog.Title className="sr-only">Меню разделов</RadixDialog.Title>
                <TeacherSidebar drawer onToggle={() => setMenuOpen(false)} onNavigate={() => setMenuOpen(false)} />
              </RadixDialog.Content>
            </RadixDialog.Portal>
          </RadixDialog.Root>
          <span className="t-mobile-title">{SECTIONS.find((s) => pathname === s.path || pathname.startsWith(`${s.path}/`))?.label || 'Кабинет преподавателя'}</span>
        </header>}
        <main className="t-main"><Outlet /></main>
      </div>
    </div>
  );
}
