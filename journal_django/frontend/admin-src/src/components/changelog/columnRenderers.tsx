import type { ReactElement } from 'react';
import { fmtDateTime, fmtDateTimeShort } from '../../lib/format';
import type { ChangelogOperation } from '../../lib/types';

// ─── Иконки действий (16px) ──────────────────────────────

const svgProps = {
  width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none',
  stroke: 'currentColor', strokeWidth: 1.8,
  strokeLinecap: 'round', strokeLinejoin: 'round',
} as const;

export const ACTION_ICONS: Record<string, ReactElement> = {
  move:   <svg {...svgProps}><polyline points="17 11 21 7 17 3"/><line x1="21" y1="7" x2="9" y2="7"/><polyline points="7 13 3 17 7 21"/><line x1="3" y1="17" x2="15" y2="17"/></svg>,
  create: <svg {...svgProps}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>,
  edit:   <svg {...svgProps}><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/></svg>,
  remove: <svg {...svgProps}><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>,
  done:   <svg {...svgProps}><polyline points="20 6 9 17 4 12"/></svg>,
  cancel: <svg {...svgProps}><circle cx="12" cy="12" r="10"/><line x1="4.9" y1="4.9" x2="19.1" y2="19.1"/></svg>,
  revert: <svg {...svgProps}><polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>,
  other:  <svg {...svgProps}><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>,
};

export function actionIcon(operation: string): ReactElement {
  if (operation === 'changelog.revert') return ACTION_ICONS.revert;
  if (operation === 'plan.reschedule' || operation.includes('schedule_change') ||
      operation === 'plan.permanent_change') return ACTION_ICONS.move;
  if (operation === 'plan.cancel') return ACTION_ICONS.cancel;
  if (operation === 'lesson.submit') return ACTION_ICONS.done;
  // Хвост ключа после «.» или «_»: payroll.personal_rate_create — тоже создание.
  if (/[._]create$/.test(operation) || operation === 'plan.extra' ||
      operation === 'plan.generate' || operation === 'payment.create') return ACTION_ICONS.create;
  if (/[._]delete$/.test(operation)) return ACTION_ICONS.remove;
  if (/[._](update|activate|deactivate|reset)$/.test(operation) ||
      operation.startsWith('plan.')) return ACTION_ICONS.edit;
  return ACTION_ICONS.other;
}

// ─── Роли по-русски (для колонки «Кто») ───────────────────────────────────────

export const ROLE_SHORT: Record<string, string> = {
  teacher: 'преподаватель',
  manager: 'менеджер',
  admin:   'админ',
};

// ─── Готовые ячейки для переиспользования в компактных списках ───────────────

export function TimeCell({ occurredAt }: { occurredAt: string }): ReactElement {
  return (
    <span className="mono" style={{ color: 'var(--text2)', fontSize: '0.8125rem' }} title={fmtDateTime(occurredAt)}>
      {fmtDateTimeShort(occurredAt)}
    </span>
  );
}

export function ActorCell({ actor }: { actor: ChangelogOperation['actor'] }): ReactElement {
  return actor ? (
    <span style={{ color: 'var(--text3)' }} title={actor.email ?? undefined}>
      {actor.name}
      {actor.role ? ` (${ROLE_SHORT[actor.role] ?? actor.role})` : ''}
    </span>
  ) : (
    <span style={{ color: 'var(--text3)' }}>Система</span>
  );
}

export function OperationCell({ operation, label }: { operation: string; label: string }): ReactElement {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--text)' }}>
      <span style={{ color: 'var(--text3)', display: 'inline-flex' }}>{actionIcon(operation)}</span>
      {label}
    </span>
  );
}
