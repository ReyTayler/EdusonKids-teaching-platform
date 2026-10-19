import { Outlet } from 'react-router-dom';
import { useEffect } from 'react';
import { useAuth } from '../../hooks/useAuth';
import { hideSplash } from '../../lib/splash';
import type { Me } from '../../providers/AuthProvider';

/**
 * Роли, которым разрешён вход в admin SPA.
 * Зеркалит серверный критерий apps/auth_app/services.py:role_matches:
 *   account_role in ('manager', 'admin', 'superadmin')
 * Реальная защита данных — на API (IsManagerOrAdmin / IsAdmin / IsSuperAdmin);
 * этот guard лишь не даёт отрисовать оболочку админки чужой роли и уводит её
 * в свой раздел.
 */
const ADMIN_ROLES: ReadonlyArray<Me['role']> = ['admin', 'manager', 'superadmin'];

function canAccessAdmin(me: Me | null): boolean {
  return !!me && ADMIN_ROLES.includes(me.role);
}

/** Куда отправить аутентифицированного, но не имеющего доступа в админку. */
function redirectFor(me: Me | null): string {
  return me?.role === 'teacher' ? '/teacher' : '/login';
}

export function AuthGate() {
  const { authenticated, me } = useAuth();

  // authenticated и me приходят вместе (AuthProvider выставляет их в одном .then),
  // но пока роль неизвестна — считаем состояние ещё не готовым.
  const ready = authenticated !== null && !(authenticated && me === null);
  const allowed = authenticated === true && canAccessAdmin(me);

  useEffect(() => {
    if (!ready) return;
    if (authenticated === false) {
      window.location.href = '/login';
      return;
    }
    if (!allowed) {
      // Аутентифицирован, но роль без доступа (например teacher) —
      // уводим в его раздел, а не показываем пустую админку.
      window.location.href = redirectFor(me);
    }
  }, [ready, authenticated, allowed, me]);

  // Стартовый экран снимаем ТОЛЬКО когда админка действительно рисуется. На
  // ветках редиректа (не залогинен / чужая роль) он остаётся на месте до
  // перехода — иначе между «погас» и «браузер ушёл» мелькнёт пустая страница.
  useEffect(() => {
    if (allowed) hideSplash();
  }, [allowed]);

  if (!ready) {
    // Не видно за стартовым экраном; остаётся на случай, если он уже снят.
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Загрузка…</div>;
  }
  // Пока идёт редирект (не залогинен или чужая роль) — ничего не рендерим,
  // чтобы каркас админки не мелькнул на экране.
  if (!allowed) return null;

  return <Outlet />;
}
