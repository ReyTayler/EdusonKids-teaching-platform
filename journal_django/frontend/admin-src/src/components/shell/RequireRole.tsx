import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import type { Role } from '../../lib/permissions';

export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { me } = useAuth();
  if (me && !roles.includes(me.role as Role)) {
    return <Navigate to="/admin/dashboard" replace />;
  }
  return <>{children}</>;
}
