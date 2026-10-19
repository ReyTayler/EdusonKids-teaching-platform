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
import type { DashboardData } from '../../../lib/types';

type Mode = 'daily' | 'monthly';

interface Row {
  key: string;        // 'YYYY-MM-DD' или 'YYYY-MM'
  recognized: number;
}

function RecognizedTooltip({ active, payload, mode }: {
  active?: boolean;
  payload?: { payload?: Row }[];
  mode: Mode;
}) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__label">
        {mode === 'daily' ? fmtDate(row.key) : fmtMonthFull(row.key)}
      </div>
      <div className="chart-tooltip__row">
        <span className="revenue-legend__swatch revenue-legend__swatch--recognized" />
        <span className="chart-tooltip__year">Recognized</span>
        <span className="chart-tooltip__value">{fmtRub(row.recognized)}</span>
      </div>
    </div>
  );
}

function Plot({ data, mode }: { data: DashboardData; mode: Mode }) {
  const rows: Row[] = mode === 'daily'
    ? data.recognized_daily.map((p) => ({ key: p.date, recognized: p.recognized }))
    : data.recognized_monthly.map((p) => ({ key: p.month, recognized: p.recognized }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
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
          tickFormatter={fmtAxisRub}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        <Tooltip
          content={<RecognizedTooltip mode={mode} />}
          cursor={mode === 'daily' ? { stroke: 'var(--line)' } : { fill: 'var(--bg3)' }}
        />
        {mode === 'daily' ? (
          <Line
            type="linear"
            dataKey="recognized"
            stroke="var(--text)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        ) : (
          <Bar
            dataKey="recognized"
            fill="var(--text)"
            radius={[3, 3, 0, 0]}
            maxBarSize={28}
            isAnimationActive={false}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/**
 * «Recognized revenue daily / monthly»: отработанные деньги (FIFO) по дню урока
 * и по месяцам периода. Одна серия — чёрная, как основная серия
 * соседнего графика. Сумма ряда равна плитке «Отработано за период».
 */
export function RecognizedChart({ data }: { data: DashboardData }) {
  const [mode, setMode] = useState<Mode>('daily');
  return (
    <Tabs
      className="revenue-chart"
      value={mode}
      onChange={(v) => setMode(v as Mode)}
      items={[
        { value: 'daily', label: 'Recognized daily', content: <Plot data={data} mode="daily" /> },
        { value: 'monthly', label: 'Recognized monthly', content: <Plot data={data} mode="monthly" /> },
      ]}
    />
  );
}
