import type { CSSProperties } from 'react';
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis } from 'recharts';
import { EmptyState } from '../../components/ui/EmptyState';
import { directionColor } from '../../lib/direction-color';
import { MONTHS_RU } from '../../lib/slots';
import type { TeacherStats } from '../../hooks/useTeacherStats';

interface Props {
  stats: TeacherStats | undefined;
}

/** 'YYYY-MM' → 'июл'. */
function shortMonth(month: string): string {
  const m = Number(month.split('-')[1]);
  return MONTHS_RU[m - 1].slice(0, 3).toLowerCase();
}

// Тултип по рецепту .pop (класс .chart-tooltip из reports.css).
function LoadTooltip({ active, payload, label }: { active?: boolean; payload?: { value?: number }[]; label?: string | number }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__label">{label}</div>
      <div className="chart-tooltip__row">
        <span className="chart-tooltip__value">{payload[0]?.value} занятий</span>
      </div>
    </div>
  );
}

/**
 * Вкладка «Обзор»: чем именно преподаватель занят и как менялась его нагрузка.
 *
 * Полоса красится цветом направления — тем же, что в DirTag и на карточке
 * группы: направление обязано выглядеть одинаково во всех разделах.
 */
export default function TeacherDirectionsBreakdown({ stats }: Props) {
  const rows = stats?.by_direction ?? [];
  const max = rows.reduce((acc, r) => Math.max(acc, r.sessions), 0);
  const series = (stats?.monthly ?? []).map((p) => ({ ...p, label: shortMonth(p.month) }));
  // График неподвижен внутри года, поэтому выбранный месяц надо чем-то
  // пометить — иначе непонятно, к какой точке относятся плитки выше.
  const selectedLabel = stats ? shortMonth(stats.month) : null;
  const yearTotal = series.reduce((sum, p) => sum + p.sessions, 0);

  return (
    <div className="tbreak">
      <section className="tbreak__col">
        <h3 className="sub-header">Направления за месяц</h3>
        {rows.length === 0 ? (
          <EmptyState hint="Выберите другой месяц стрелками над плитками.">
            Занятий за этот месяц нет
          </EmptyState>
        ) : (
          <div className="tdir-list">
            {rows.map((r) => (
              <div
                key={r.direction_id}
                className="tdir"
                style={{ '--entity-c': directionColor(r.color || r.name) } as CSSProperties}
              >
                <div className="tdir__name">{r.name}</div>
                <div className="tdir__bar">
                  <div
                    className="tdir__fill"
                    style={{ width: max ? `${(r.sessions / max) * 100}%` : '0%' }}
                  />
                </div>
                <div className="tdir__count">{r.sessions}</div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="tbreak__col">
        <h3 className="sub-header">
          Занятий за {stats?.year ?? '—'} год
          {yearTotal > 0 && <span className="count-badge">{yearTotal}</span>}
        </h3>
        <div className="tspark">
          <ResponsiveContainer width="100%" height={140}>
            <AreaChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
              <defs>
                <linearGradient id="teacher-load" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
                </linearGradient>
              </defs>
              {/* Все 12 подписей, а не preserveStartEnd: ось теперь неподвижна,
                  и месяц под точкой — единственный ориентир, где ты находишься. */}
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                interval={0}
                tick={{ fontSize: 11, fill: 'var(--text3)', fontFamily: 'var(--font-mono)' }}
              />
              {selectedLabel && (
                <ReferenceLine
                  x={selectedLabel}
                  stroke="var(--accent)"
                  strokeDasharray="3 3"
                  strokeOpacity={0.7}
                />
              )}
              <Tooltip content={<LoadTooltip />} cursor={{ stroke: 'var(--line)' }} />
              <Area
                type="monotone"
                dataKey="sessions"
                stroke="var(--accent)"
                strokeWidth={2}
                fill="url(#teacher-load)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>
  );
}
