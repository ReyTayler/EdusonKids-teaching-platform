import { useState } from 'react';
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Tabs } from '../../../components/ui/Tabs';
import { fmtRub, fmtDate } from '../../../lib/format';
import { AXIS_TICK, fmtAxisRub, fmtDayTick, fmtMonthFull, fmtMonthTick } from './chartFormat';
import type { RevenueData } from '../../../lib/types';

type Mode = 'daily' | 'monthly';

/** Серии графика; solo === null — показаны все. */
type SeriesKey = 'revenue' | 'orders' | 'aov';

const SERIES: { key: SeriesKey; label: string }[] = [
  { key: 'revenue', label: 'Revenue' },
  { key: 'orders', label: 'Orders' },
  { key: 'aov', label: 'AOV' },
];

interface Row {
  key: string;      // 'YYYY-MM-DD' или 'YYYY-MM'
  revenue: number;
  orders: number;
  // Сервер отдаёт null, когда оплат не было (делить не на что); на графике это
  // ноль — линия должна опускаться, а не рваться (решение пользователя 2026-09-30).
  aov: number;
}

interface TooltipProps {
  active?: boolean;
  payload?: { payload?: Row }[];
  mode: Mode;
  visible: (key: SeriesKey) => boolean;
}

function RevenueTooltip({ active, payload, mode, visible }: TooltipProps) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const text: Record<SeriesKey, string> = {
    revenue: fmtRub(row.revenue),
    orders: String(row.orders),
    aov: fmtRub(row.aov),
  };
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__label">
        {mode === 'daily' ? fmtDate(row.key) : fmtMonthFull(row.key)}
      </div>
      {SERIES.filter((s) => visible(s.key)).map((s) => (
        <div className="chart-tooltip__row" key={s.key}>
          <span className={'revenue-legend__swatch revenue-legend__swatch--' + s.key} />
          <span className="chart-tooltip__year">{s.label}</span>
          <span className="chart-tooltip__value">{text[s.key]}</span>
        </div>
      ))}
    </div>
  );
}

function Plot({ data, mode, solo, onPick }: {
  data: RevenueData;
  mode: Mode;
  solo: SeriesKey | null;
  onPick: (key: SeriesKey) => void;
}) {
  const visible = (key: SeriesKey) => solo === null || solo === key;
  const rows: Row[] = mode === 'daily'
    ? data.daily.map((p) => ({ key: p.date, revenue: p.revenue, orders: p.orders, aov: p.aov ?? 0 }))
    : data.monthly.map((p) => ({ key: p.month, revenue: p.revenue, orders: p.orders, aov: p.aov ?? 0 }));

  return (
    <>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={rows} margin={{ top: 8, right: 0, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--line)" />
          <XAxis
            dataKey="key"
            tickFormatter={mode === 'daily' ? fmtDayTick : fmtMonthTick}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: 'var(--line)' }}
            minTickGap={24}
          />
          <YAxis
            yAxisId="revenue"
            hide={!visible('revenue')}
            tickFormatter={fmtAxisRub}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={48}
          />
          <YAxis
            yAxisId="orders"
            hide={!visible('orders')}
            orientation="right"
            allowDecimals={false}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={36}
          />
          {/* Средний чек — рубли, но на порядок меньше суммы за день: на общей
              с Revenue шкале лёг бы плоской линией у нуля. Своя шкала от нуля,
              скрыта — третья ось перегрузила бы график; точное значение в подсказке. */}
          <YAxis
            yAxisId="aov"
            hide={solo !== 'aov'}
            domain={[0, 'auto']}
            tickFormatter={fmtAxisRub}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={48}
          />
          <Tooltip
            content={<RevenueTooltip mode={mode} visible={visible} />}
            cursor={mode === 'daily' ? { stroke: 'var(--line)' } : { fill: 'var(--bg3)' }}
          />
          {mode === 'daily' ? (
            <Line
              yAxisId="revenue"
              hide={!visible('revenue')}
              type="linear"
              dataKey="revenue"
              stroke="var(--text)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          ) : (
            <Bar
              yAxisId="revenue"
              hide={!visible('revenue')}
              dataKey="revenue"
              fill="var(--text)"
              radius={[3, 3, 0, 0]}
              maxBarSize={28}
              isAnimationActive={false}
            />
          )}
          <Line
            yAxisId="aov"
            hide={!visible('aov')}
            type="linear"
            dataKey="aov"
            stroke="var(--text2)"
            strokeWidth={1.75}
            strokeDasharray="2 3"
            dot={false}
            activeDot={{ r: 4, fill: 'var(--text2)' }}
            isAnimationActive={false}
          />
          <Line
            yAxisId="orders"
            hide={!visible('orders')}
            type="linear"
            dataKey="orders"
            stroke="var(--text3)"
            strokeWidth={2}
            strokeDasharray="6 4"
            dot={false}
            activeDot={{ r: 4, fill: 'var(--text3)' }}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="revenue-legend">
        {SERIES.map((s) => (
          <button
            key={s.key}
            type="button"
            className={
              'revenue-legend__item'
              + (solo === s.key ? ' revenue-legend__item--active' : '')
              + (visible(s.key) ? '' : ' revenue-legend__item--dimmed')
            }
            aria-pressed={solo === s.key}
            title={solo === s.key ? 'Показать все серии' : 'Показать только ' + s.label}
            onClick={() => onPick(s.key)}
          >
            <span className={'revenue-legend__swatch revenue-legend__swatch--' + s.key} />
            {s.label}
          </button>
        ))}
      </div>
    </>
  );
}

/**
 * «Revenue daily / Revenue monthly»: сумма поступлений (чёрная линия или
 * столбики, левая ось), число оплат (серый пунктир, правая ось) и средний чек
 * AOV (серая точечная линия, своя скрытая шкала) за период фильтра. Клик по подписи
 * серии внизу оставляет только её (повторный клик возвращает все); у выбранного
 * в одиночку AOV появляется собственная шкала слева.
 */
export function RevenueChart({ data }: { data: RevenueData }) {
  const [mode, setMode] = useState<Mode>('daily');
  // Выбор серии держим здесь, а не в Plot: Tabs монтируют только активную
  // панель, поэтому внутри Plot он сбрасывался бы при смене daily/monthly.
  const [solo, setSolo] = useState<SeriesKey | null>(null);
  const pick = (key: SeriesKey) => setSolo((cur) => (cur === key ? null : key));
  const plot = (m: Mode) => <Plot data={data} mode={m} solo={solo} onPick={pick} />;
  return (
    <Tabs
      className="revenue-chart"
      value={mode}
      onChange={(v) => setMode(v as Mode)}
      items={[
        { value: 'daily', label: 'Revenue daily', content: plot('daily') },
        { value: 'monthly', label: 'Revenue monthly', content: plot('monthly') },
      ]}
    />
  );
}
