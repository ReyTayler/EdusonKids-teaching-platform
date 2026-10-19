import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Dialog } from '../../components/ui/Dialog';
import { PageLoading } from '../../components/ui/Skeleton';
import { useAuth } from '../../hooks/useAuth';
import { canRevertChangelog, type Role } from '../../lib/permissions';
import { useChangelogDetail } from '../../hooks/useChangelog';
import { DiffView, snapshotToDiff } from '../../components/changelog/DiffView';
import { ApiError } from '../../lib/api';
import { fmtDateTime } from '../../lib/format';
import { CHANGELOG_ENTITY_LABELS, CHANGELOG_OPERATION_LABELS } from '../../lib/labels';
import type {
  ChangelogEntitySummary,
  ChangelogEvent,
  ChangelogFieldChange,
  ChangelogOperation,
} from '../../lib/types';

// Сущности с detail-маршрутом в SPA (проверено по App.tsx).
const ENTITY_ROUTES: Record<string, string> = {
  student:   '/admin/students',
  group:     '/admin/groups',
  teacher:   '/admin/teachers',
  direction: '/admin/directions',
  lesson:    '/admin/lessons',
};

const LABEL_BADGES: Record<ChangelogEvent['label'], { cls: string; text: string }> = {
  insert: { cls: 'status-badge--positive', text: 'создание' },
  update: { cls: 'status-badge--info',     text: 'изменение' },
  delete: { cls: 'status-badge--negative', text: 'удаление' },
};

// Порог сворачивания массовых групп и клиентский cap на рендер событий.
const COLLAPSE_AT = 5;
const RENDER_CAP  = 200;

/** Одна строка человекочитаемого изменения поля: «label: old → new» или «label: значение». */
function ChangeRow({ change }: { change: ChangelogFieldChange }) {
  const { label, old, new: next } = change;
  const dash = (v: string | null) => (v === null || v === '' ? '—' : v);
  return (
    <div className="changelog-changes__row">
      <dt className="changelog-changes__label">{label}</dt>
      <dd className="changelog-changes__value">
        {old === null || next === null ? (
          <span>{dash(old ?? next)}</span>
        ) : (
          <>
            <span className="changelog-changes__old">{dash(old)}</span>
            <span className="changelog-changes__arrow" aria-hidden="true">→</span>
            <span>{dash(next)}</span>
          </>
        )}
      </dd>
    </div>
  );
}

function EventRow({ ev, onNavigate }: { ev: ChangelogEvent; onNavigate: () => void }) {
  const badge = LABEL_BADGES[ev.label];
  const route = ENTITY_ROUTES[ev.entity];
  const entityLabel = CHANGELOG_ENTITY_LABELS[ev.entity] ?? ev.entity;
  const diff = ev.label === 'update'
    ? (ev.diff ?? {})
    : snapshotToDiff(ev.data, ev.label);

  const human = ev.human;
  const fallbackTitle = `${entityLabel}${ev.obj_id != null ? ` #${String(ev.obj_id)}` : ''}`;
  const title = human?.title ?? fallbackTitle;
  // human.text дублирует title дословно у многих операций — показываем строкой,
  // только если он реально добавляет информацию.
  const showText = !!human && human.text.trim() !== human.title.trim();
  const canNavigate = route && ev.obj_id != null && ev.label !== 'delete';

  return (
    <div className="changelog-event">
      <div className="changelog-event__head">
        <span className={`status-badge ${badge.cls}`}>{badge.text}</span>
        {canNavigate ? (
          <Link className="btn-link changelog-event__title" to={`${route}/${ev.obj_id}`} onClick={onNavigate}>
            {title}
          </Link>
        ) : (
          <span className="changelog-event__title">{title}</span>
        )}
      </div>

      {human ? (
        <>
          {showText && <div className="changelog-event__text">{human.text}</div>}
          {human.changes.length > 0 ? (
            <dl className="changelog-changes">
              {human.changes.map((c, i) => <ChangeRow key={i} change={c} />)}
            </dl>
          ) : (
            <div className="changelog-event__empty">Нет изменённых полей</div>
          )}
          <details className="details-toggle">
            <summary>Техническое описание</summary>
            <div className="changelog-event__diff-wrap">
              <DiffView diff={diff} />
            </div>
          </details>
        </>
      ) : (
        <>
          {ev.description && <div className="changelog-event__text">{ev.description}</div>}
          {ev.description ? (
            <details className="details-toggle">
              <summary>Техническое описание</summary>
              <div className="changelog-event__diff-wrap">
                <DiffView diff={diff} />
              </div>
            </details>
          ) : (
            <DiffView diff={diff} />
          )}
        </>
      )}
    </div>
  );
}

function EntityGroup({ entity, events, onNavigate }: {
  entity: string;
  events: ChangelogEvent[];
  onNavigate: () => void;
}) {
  const [expanded, setExpanded] = useState(events.length <= COLLAPSE_AT);
  const entityLabel = CHANGELOG_ENTITY_LABELS[entity] ?? entity;

  return (
    <section style={{ display: 'grid', gap: 'var(--space-3)' }}>
      <h3 style={{ margin: 0, fontSize: '0.9375rem', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        {entityLabel}
        <span style={{ color: 'var(--text3)', fontWeight: 400 }}>
          {events.length > 1 ? `${events.length} записей` : ''}
        </span>
        {!expanded && (
          <button type="button" className="btn-link" onClick={() => setExpanded(true)}>
            Показать
          </button>
        )}
      </h3>
      {expanded && events.map((ev, i) => <EventRow key={i} ev={ev} onNavigate={onNavigate} />)}
    </section>
  );
}

/** Сводка сущностей из событий (для передачи в confirm-диалог отката). */
function summarizeEntities(events: ChangelogEvent[]): ChangelogEntitySummary[] {
  const acc = new Map<string, ChangelogEntitySummary>();
  for (const ev of events) {
    const cur = acc.get(ev.entity) ?? { entity: ev.entity, inserts: 0, updates: 0, deletes: 0 };
    if (ev.label === 'insert') cur.inserts += 1;
    else if (ev.label === 'update') cur.updates += 1;
    else cur.deletes += 1;
    acc.set(ev.entity, cur);
  }
  return [...acc.values()];
}

export function ChangelogDetailModal({ contextId, onClose, onRevert, readOnly = false }: {
  contextId: string;
  onClose: () => void;
  onRevert?: (op: ChangelogOperation) => void;
  readOnly?: boolean;
}) {
  const { me } = useAuth();
  const { data, isLoading, error } = useChangelogDetail(contextId);
  const [renderCap, setRenderCap] = useState(RENDER_CAP);

  const groups = useMemo(() => {
    if (!data) return [];
    const capped = data.events.slice(0, renderCap);
    const byEntity = new Map<string, ChangelogEvent[]>();
    for (const ev of capped) {
      const list = byEntity.get(ev.entity) ?? [];
      list.push(ev);
      byEntity.set(ev.entity, list);
    }
    return [...byEntity.entries()];
  }, [data, renderCap]);

  const notFound = error instanceof ApiError && error.status === 404;
  const hiddenCount = data ? data.events.length - Math.min(renderCap, data.events.length) : 0;
  // Причина — с бэкенда (готовый русский текст); фолбэк на случай старого кэша без поля.
  const notRevertableReason = data?.not_revertable_reason
    ?? 'изменения этой операции недоступны для отката';

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={data ? (CHANGELOG_OPERATION_LABELS[data.operation] ?? data.operation) : 'Запись журнала'}
      wide
      footer={
        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <button type="button" className="btn-cancel" onClick={onClose}>
            Закрыть
          </button>
          {!readOnly && data && canRevertChangelog(me?.role as Role) && data.revertable && (
            <button
              type="button"
              className="btn-danger"
              onClick={() =>
                onRevert?.({
                  ...data,
                  entities: summarizeEntities(data.events),
                  events_total: data.events.length,
                })
              }
            >
              Откатить операцию
            </button>
          )}
        </div>
      }
    >
      {isLoading && <PageLoading />}
      {notFound && <p style={{ color: 'var(--text3)' }}>Операция не найдена.</p>}
      {data && (
        <div style={{ display: 'grid', gap: 'var(--space-4)' }}>
          <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
            <div style={{ fontWeight: 600 }}>{data.summary}</div>
            <div style={{ color: 'var(--text3)', fontSize: '0.8125rem', display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
              <span>{fmtDateTime(data.occurred_at)}</span>
              {data.actor
                ? <span className="mono">{data.actor.email}</span>
                : <span>Система</span>}
              {data.url && <span className="mono">{data.method} {data.url}</span>}
              <span className="mono" title="ID операции" style={{ fontSize: '0.6875rem' }}>{data.id}</span>
            </div>
          </div>

          {!data.revertable && (
            <div className="entity-card" style={{ opacity: 0.85, padding: 'var(--space-3)', color: 'var(--text2)' }}>
              Эта операция не может быть отменена: {notRevertableReason}.
            </div>
          )}

          {groups.map(([entity, events]) => (
            <EntityGroup key={entity} entity={entity} events={events} onNavigate={onClose} />
          ))}
          {hiddenCount > 0 && (
            <button type="button" className="btn-link" onClick={() => setRenderCap((c) => c + RENDER_CAP)}>
              Показать ещё ({hiddenCount})
            </button>
          )}
        </div>
      )}
    </Dialog>
  );
}
