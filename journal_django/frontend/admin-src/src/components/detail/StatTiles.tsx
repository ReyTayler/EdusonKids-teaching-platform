import type { ReactNode } from 'react';

export type StatTone = 'default' | 'ok' | 'warn' | 'danger' | 'accent';

export interface StatTile {
  label: string;
  value: ReactNode;
  /** Контекст под значением: «из 56 по плану», «2 просрочено». Без него число немое. */
  sub?: ReactNode;
  /** Тон применяется ТОЛЬКО к значению и только когда несёт смысл (норма/риск). */
  tone?: StatTone;
  subTone?: StatTone;
}

/**
 * Ряд ключевых показателей карточки сущности.
 *
 * Классы намеренно свои (`dstat-*`), а не общие `.kpi-card`: последние определены
 * сразу в трёх файлах (dashboard.css, shared/calendar/calendar.css, detail.css),
 * побеждает импортированный последним — из-за чего KPI ученика раскладывались
 * в сетку календаря (6 колонок) вместо своей.
 */
export function StatTiles({ items }: { items: StatTile[] }) {
  return (
    <div className="dstat-row">
      {items.map((t) => (
        <div key={t.label} className="dstat">
          {/* Как в Eduson (.stat): число сверху, подпись под ним. */}
          <div className={`dstat__value dstat__value--${t.tone || 'default'}`}>{t.value}</div>
          <div className="dstat__label">{t.label}</div>
          {t.sub != null && (
            <div className={`dstat__sub dstat__sub--${t.subTone || 'default'}`}>{t.sub}</div>
          )}
        </div>
      ))}
    </div>
  );
}
