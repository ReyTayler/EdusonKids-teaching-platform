import { Link } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList } from 'recharts';
import { useRenewalAnalytics, useRenewalMonths } from '../../hooks/useRenewalAnalytics';
import { KpiCard } from '../dashboard/KpiCard';
import { PageHeader } from '../../components/shell/PageHeader';

// '2026-07' → «июль 2026»
function fmtMonth(ym: string): string {
  const [y, m] = ym.split('-').map(Number);
  if (!y || !m) return ym;
  const s = new Intl.DateTimeFormat('ru', { month: 'long', year: 'numeric' })
    .format(new Date(Date.UTC(y, m - 1, 1)));
  return s.replace(' г.', '');
}

interface TooltipRow { payload?: { label?: string; cnt?: number } }
function FunnelTooltip({ active, payload }: { active?: boolean; payload?: TooltipRow[] }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__label">{row.label}</div>
      <div className="chart-tooltip__row">
        <span className="chart-tooltip__value">{row.cnt} сделок</span>
      </div>
    </div>
  );
}

export default function RenewalAnalyticsPage() {
  const { data, isLoading } = useRenewalAnalytics();
  const { data: monthsData } = useRenewalMonths();
  const months = monthsData?.months || [];

  return (
    <div className="renewals-page">
      <PageHeader
        title="Аналитика продлений"
        crumbs={[{ label: 'Продления', to: '/admin/renewals' }, { label: 'Аналитика' }]}
      />

      {isLoading || !data ? (
        <div className="renewal-board--loading">Загружаем аналитику…</div>
      ) : (
        <>
          <div className="renewals-page__kpis">
            <KpiCard
              label="Renewal rate (30 дн.)"
              value={data.renewal_rate_30d != null ? `${data.renewal_rate_30d}%` : '—'}
              tone={data.renewal_rate_30d != null && data.renewal_rate_30d >= 70 ? 'info' : 'default'}
            />
            <KpiCard label="Продлили за 30 дн." value={String(data.won_30d)} />
            <KpiCard label="Ушли за 30 дн." value={String(data.lost_30d)} tone={data.lost_30d > 0 ? 'warning' : 'default'} />
          </div>

          <section className="chart-card">
            <div className="chart-card__head">
              <h3 className="chart-card__title">Открытые сделки по стадиям</h3>
            </div>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={data.stages} margin={{ top: 16, right: 8, left: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis
                  dataKey="label"
                  tick={{ fill: 'var(--text3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--line)' }}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fill: 'var(--text3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                  tickLine={false}
                  axisLine={false}
                  width={32}
                />
                <Tooltip content={<FunnelTooltip />} cursor={{ fill: 'var(--bg3)' }} />
                <Bar dataKey="cnt" fill="var(--accent)" radius={[4, 4, 0, 0]} maxBarSize={48}>
                  <LabelList dataKey="cnt" position="top" style={{ fill: 'var(--text3)', fontSize: 11, fontFamily: 'var(--font-mono)' }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </section>

          {months.length > 0 && (
            <section className="chart-card">
              <div className="chart-card__head">
                <h3 className="chart-card__title">Продления по месяцам</h3>
                <span className="chart-card__hint">
                  Месяц = когда отработан 4-й урок цикла (оплатившие заранее — по дате оплаты)
                </span>
              </div>
              <div className="renewal-months__scroll">
                <table className="renewal-months">
                  <thead>
                    <tr>
                      <th>Месяц</th>
                      <th>Созрело</th>
                      <th>Продлено</th>
                      <th>Ушло</th>
                      <th>В работе</th>
                      <th>Конверсия</th>
                    </tr>
                  </thead>
                  <tbody>
                    {months.map((m) => (
                      <tr key={m.month}>
                        <td className="renewal-months__month">{fmtMonth(m.month)}</td>
                        <td>{m.matured}</td>
                        <td>{m.won}</td>
                        <td>{m.lost}</td>
                        <td>{m.in_progress}</td>
                        <td>{m.conversion != null ? `${m.conversion}%` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
