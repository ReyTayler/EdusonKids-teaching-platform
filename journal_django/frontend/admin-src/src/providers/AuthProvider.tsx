import { createContext, useState, useEffect, type ReactNode } from 'react';
import { api, ApiError } from '../lib/api';

export interface Me {
  account_id: number; email: string; role: 'teacher' | 'manager' | 'admin' | 'superadmin';
  teacher_id: number | null; name: string; twofa_enabled: boolean;
}
export interface AuthState {
  authenticated: boolean | null;
  me: Me | null;
  logout: () => Promise<void>;
}
export const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    api<Me>('GET', '/api/auth/me').then(
      (m) => { setMe(m); setAuthenticated(true); },
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 401) setAuthenticated(false);
        else { console.error(e); setAuthenticated(false); }
      },
    );
  }, []);

  useEffect(() => {
    const onExpired = () => { setAuthenticated(false); setMe(null); };
    window.addEventListener('admin:auth-expired', onExpired);
    return () => window.removeEventListener('admin:auth-expired', onExpired);
  }, []);

  const logout = async () => {
    try { await api('POST', '/api/auth/logout'); } catch (_) {}
    setAuthenticated(false); setMe(null);
    window.location.href = '/login';
  };

  return <AuthContext.Provider value={{ authenticated, me, logout }}>{children}</AuthContext.Provider>;
}
