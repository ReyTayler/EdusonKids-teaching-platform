import { NavLink } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { Dialog } from '../ui/Dialog';
import { type Role } from '../../lib/permissions';
import { NAV_ICONS, NAV_PINNED, NAV_GROUPS } from './navConfig';
import { ExtraLessonsBadge } from './ExtraLessonsBadge';
import { LogoutButton, SidebarUser } from './Sidebar';
import { ThemeToggle } from './ThemeToggle';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Мобильная навигация: общий Dialog (Radix — ловушка фокуса, возврат фокуса на
 * кнопку меню, Esc, блокировка прокрутки) со списком групп и их значков.
 * Аккордеона здесь нет: на телефоне все разделы одним прокручиваемым списком
 * быстрее, чем раскрывать группы по одной.
 */
export function SidebarDrawer({ open, onOpenChange }: Props) {
  const { me } = useAuth();
  const role = me?.role as Role | undefined;
  const close = () => onOpenChange(false);

  const pinned = NAV_PINNED.filter((it) => !it.can || it.can(role));

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Разделы">
      <nav className="side-drawer" aria-label="Разделы">
        <div className="side-drawer__group">
          {pinned.map((it) => (
            <NavLink
              key={it.key}
              to={it.path}
              onClick={close}
              className={({ isActive }) => `side-top${isActive ? ' side-top--active' : ''}`}
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
            <div key={group.key} className="side-drawer__group">
              <div className="side-drawer__title">
                {NAV_ICONS[group.icon]}
                <span>{group.title}</span>
              </div>
              {items.map((it) => (
                <NavLink
                  key={it.key}
                  to={it.path}
                  onClick={close}
                  className={({ isActive }) => `side-item${isActive ? ' side-item--active' : ''}`}
                >
                  <span className="side-item__label">{it.label}</span>
                  {it.key === 'extra-lessons' && <ExtraLessonsBadge />}
                </NavLink>
              ))}
            </div>
          );
        })}
        <div className="side-drawer__foot">
          <SidebarUser />
          <ThemeToggle className="side-btn" showLabel />
          <LogoutButton />
        </div>
      </nav>
    </Dialog>
  );
}
