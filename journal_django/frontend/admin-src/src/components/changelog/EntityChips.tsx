import { CHANGELOG_ENTITY_LABELS } from '../../lib/labels';
import type { ChangelogEntitySummary } from '../../lib/types';

/**
 * Чипы «затронутые сущности» операции журнала: «Урок ×1», «Посещаемость ×8».
 * Используется в колонке ленты, шапке карточки и confirm-модалке отката.
 */
export function EntityChips({ entities, max = 3 }: {
  entities: ChangelogEntitySummary[];
  max?: number;
}) {
  if (!entities.length) {
    return <span style={{ color: 'var(--text3)' }}>без изменений</span>;
  }
  const shown = entities.slice(0, max);
  const rest = entities.length - shown.length;
  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 'var(--space-1)' }}>
      {shown.map((e) => {
        const label = CHANGELOG_ENTITY_LABELS[e.entity] ?? e.entity;
        const total = e.inserts + e.updates + e.deletes;
        return (
          <span key={e.entity} className="status-badge status-badge--muted" title={chipTitle(e)}>
            {label}{total > 1 ? ` ×${total}` : ''}
          </span>
        );
      })}
      {rest > 0 && <span className="status-badge status-badge--muted">+{rest}</span>}
    </span>
  );
}

function chipTitle(e: ChangelogEntitySummary): string {
  const parts: string[] = [];
  if (e.inserts) parts.push(`создано: ${e.inserts}`);
  if (e.updates) parts.push(`изменено: ${e.updates}`);
  if (e.deletes) parts.push(`удалено: ${e.deletes}`);
  return parts.join(', ');
}
