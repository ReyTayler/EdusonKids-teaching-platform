import { type ReactNode } from 'react';

interface Props { children: ReactNode; }

export function Pill({ children }: Props) {
  return <span className="pill">{children}</span>;
}
