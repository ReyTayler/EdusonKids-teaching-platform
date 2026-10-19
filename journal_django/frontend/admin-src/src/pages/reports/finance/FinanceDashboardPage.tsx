import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useDashboard } from '../../../hooks/useDashboard';
import { useRevenue } from '../../../hooks/useRevenue';
import { fmtRub } from '../../../lib/format';
import { PageLoading } from '../../../components/ui/Skeleton';
import { DateInput } from '../../../components/form/DateInput';
import { PageHeader } from '../../../components/shell/PageHeader';
import { KpiCard } from '../../dashboard/KpiCard';

// Lazy: Recharts грузится отдельным чанком, не блокирует первый показ плиток.
const RevenueChart = lazy(() =>
  import('./RevenueChart').then((m) => ({ default: m.RevenueChart })),
);
const RecognizedChart = lazy(() =>
  import('./RecognizedChart').then((m) => ({ default: m.RecognizedChart })),
);
const ProductsTable = lazy(() =>
  import('./ProductsTable').then((m) => ({ default: m.ProductsTable })),
);

function signedRub(v: number): string {
  return v > 0 ? `+${fmtRub(v)}` : fmtRub(v);
}

/**
 * Дашборд «Финансы» в разделе «Отчёты». Фильтр по дате оплаты общий для всей
 * страницы; без него сервер берёт последние 3 месяца по сегодня и возвращает
 * применённые границы — ими и заполняются поля дат.
 * Сверху — Revenue / Orders / AOV и график поступлений, ниже — Recognized
 * revenue (отработанное по FIFO) тем же видом, ниже — сводная таблица по курсам. Графики «Выручка/Отработано по
 * месяцам» со сравнением годов убраны 2026-09-30: их заменили ряды за период. поступлений по дням и месяцам
 * (спека 2026-09-18-revenue-dashboard-design), ниже — FIFO-сводка и сравнение по годам.
 */
export default function FinanceDashboardPage() {
  const [params, setParams] = useSearchParams();
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const hasRange = Boolean(from || to);

  const setParam = (key: 'from' | 'to', value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const reset = () => {
    const next = new URLSearchParams(params);
    next.delete('from');
    next.delete('to');
    setParams(next, { replace: true });
  };

  const range = { from: from || undefined, to: to || undefined };
  const revenue = useRevenue(range);
  const summary = useDashboard(range);

  // Поля дат показывают фактический период (дефолт тоже), пока пользователь не задал свой.
  const shownFrom = from || (hasRange ? '' : revenue.data?.from ?? '');
  const shownTo = to || (hasRange ? '' : revenue.data?.to ?? '');

  return (
    <div className="finance-dashboard">
      <PageHeader
        title="Финансы"
        sub="Поступления и отработанное по FIFO. По умолчанию — последние 3 месяца."
      />

      <div className="payroll-range">
        <label>Дата оплаты:</label>
        <DateInput value={shownFrom} onChange={(e) => setParam('from', e.target.value)} placeholder="от" />
        <span className="payroll-range__sep">—</span>
        <DateInput value={shownTo} onChange={(e) => setParam('to', e.target.value)} placeholder="до" />
        <button className="btn-secondary" onClick={reset} disabled={!hasRange}>Сбросить</button>
      </div>

      {revenue.isLoading ? (
        <PageLoading />
      ) : revenue.isError || !revenue.data ? (
        <div className="page-error">Не удалось загрузить поступления</div>
      ) : (
        <div className={`finance-overview${revenue.isPlaceholderData ? ' finance-overview--stale' : ''}`}>
          <div className="finance-overview__totals">
            <KpiCard
              className="finance-overview__revenue"
              label="Revenue"
              value={fmtRub(revenue.data.revenue)}
              hint="сумма оплат за период"
            />
            <div className="finance-overview__pair">
              <KpiCard label="Orders" value={String(revenue.data.orders)} hint="оплат за период" tone="info" />
              <KpiCard
                className="finance-overview__aov"
                label="AOV"
                value={revenue.data.aov === null ? '—' : fmtRub(revenue.data.aov)}
                hint="средний чек: Revenue / Orders"
              />
            </div>
          </div>
          <section className="chart-card finance-overview__chart">
            <Suspense fallback={<PageLoading />}>
              <RevenueChart data={revenue.data} />
            </Suspense>
          </section>
        </div>
      )}

      {summary.isLoading ? (
        <PageLoading />
      ) : summary.isError || !summary.data ? (
        <div className="page-error">Не удалось загрузить сводку</div>
      ) : (
        <div className="finance-overview">
          <div className="finance-overview__totals">
            <KpiCard
              className="finance-overview__recognized"
              label="Recognized revenue"
              value={fmtRub(summary.data.worked_off_month)}
              hint="отработано за период, FIFO"
            />
            <div className="finance-overview__pair">
              <KpiCard
                label="Авансы за период"
                value={signedRub(summary.data.carryover_month)}
                hint="Revenue − Recognized"
                tone={summary.data.carryover_month < 0 ? 'warning' : 'info'}
              />
              <KpiCard
                label="Остаток всего"
                value={fmtRub(summary.data.deferred_total)}
                hint="сейчас, не отработано"
              />
            </div>
          </div>
          <section className="chart-card finance-overview__chart">
            <Suspense fallback={<PageLoading />}>
              <RecognizedChart data={summary.data} />
            </Suspense>
          </section>
        </div>
      )}

      {revenue.data && (
        <section className="dash-card">
          <div className="dash-card__head">
            <h2 className="dash-card__title">Курсы</h2>
            <span className="products-table__note">за выбранный период</span>
          </div>
          <Suspense fallback={<PageLoading />}>
            <ProductsTable rows={revenue.data.products} />
          </Suspense>
        </section>
      )}
    </div>
  );
}
