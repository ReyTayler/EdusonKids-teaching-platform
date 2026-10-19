import { useState } from 'react';

/**
 * Diff «поле → было → стало» для события журнала изменений.
 * diff — pgh_diff бэкенда: { field: [old, new] }.
 * Переиспользуем и для снапшотов insert/delete (см. snapshotToDiff ниже).
 * Внешний вид — в styles/pages/changelog.css: удалённое (bad-тон) и добавленное
 * (ok-тон) подсвечены светлыми заливками; пустая сторона (—) не красится.
 */
export function DiffView({ diff }: { diff: Record<string, [unknown, unknown]> }) {
  const entries = Object.entries(diff);
  if (!entries.length) {
    return <div className="diff-view__empty">Нет изменённых полей</div>;
  }
  return (
    <table className="diff-view">
      <thead>
        <tr>
          {['Поле', 'Было', 'Стало'].map((h) => (
            <th key={h}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {entries.map(([field, [before, after]]) => (
          <tr key={field}>
            <td><span className="mono diff-view__field">{field}</span></td>
            <td className={isEmpty(before) ? undefined : 'diff-view__del'}><Value v={before} /></td>
            <td className={isEmpty(after) ? undefined : 'diff-view__ins'}><Value v={after} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function isEmpty(v: unknown): boolean {
  return v === null || v === undefined || v === '';
}

const TRUNCATE_AT = 200;

function Value({ v }: { v: unknown }) {
  const [expanded, setExpanded] = useState(false);
  if (v === null || v === undefined || v === '') {
    return <span className="diff-view__none">—</span>;
  }
  if (typeof v === 'boolean') {
    return <span>{v ? 'да' : 'нет'}</span>;
  }
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
  if (s.length <= TRUNCATE_AT || expanded) {
    return <span className="mono diff-view__val">{s}</span>;
  }
  return (
    <span className="mono diff-view__val">
      {s.slice(0, TRUNCATE_AT)}…{' '}
      <button type="button" className="btn-link" onClick={() => setExpanded(true)}>
        показать полностью
      </button>
    </span>
  );
}

/**
 * Снапшот insert/delete → формат diff:
 *   insert: — → значение;  delete: значение → —.
 * null-поля опускаются (шум), pgh_* полей в data нет — бэкенд их не отдаёт.
 */
export function snapshotToDiff(
  data: Record<string, unknown>,
  kind: 'insert' | 'delete',
): Record<string, [unknown, unknown]> {
  const out: Record<string, [unknown, unknown]> = {};
  for (const [k, v] of Object.entries(data)) {
    if (v === null || v === undefined) continue;
    out[k] = kind === 'insert' ? [null, v] : [v, null];
  }
  return out;
}
