import { useMemo, useState } from 'react';
import { fmtRub } from '../../../lib/format';
import type { ProductRow } from '../../../lib/types';

type SortKey = 'direction' | 'purchases' | 'months' | 'revenue' | 'asp' | 'arpm';
type SortDir = 'asc' | 'desc';

const COLUMNS: { key: SortKey; label: string; hint: string; numeric: boolean }[] = [
  { key: 'direction', label: 'Направление', hint: 'Курс, по которому пришли деньги', numeric: false },
  { key: 'purchases', label: 'Purchases', hint: 'Количество покупок курса за период', numeric: true },
  { key: 'months', label: 'Months paid', hint: 'Оплачено месяцев: абонементы, а поштучные уроки — по 0,25', numeric: true },
  { key: 'revenue', label: 'Revenue', hint: 'Все поступления по курсу за период, без возвратов', numeric: true },
  { key: 'asp', label: 'ASP', hint: 'Средняя цена покупки: Revenue / Purchases', numeric: true },
  { key: 'arpm', label: 'ARPM', hint: 'Средняя цена месяца: среднее по заказам от «цена ÷ срок»', numeric: true },
];

const LEGACY = 'Без направления';

/** Число для сортировки: «—» (null) всегда внизу, независимо от направления. */
function sortValue(row: ProductRow, key: SortKey): number | string {
  if (key === 'direction') return row.direction ?? LEGACY;
  return row[key] ?? Number.NEGATIVE_INFINITY;
}

function fmtMonths(v: number): string {
  return v.toLocaleString('ru', { maximumFractionDigits: 2 });
}

/**
 * Денежная ячейка с полоской: доля значения от максимума ПО СВОЕМУ столбцу.
 * У Revenue, ASP и ARPM разные порядки величин, поэтому общая шкала сделала бы
 * две из трёх полосок незаметными. Цвет повторяет цвет метрики на графиках.
 */
function MoneyCell({ value, max, tone }: {
  value: number | null;
  max: number;
  tone: 'revenue' | 'asp' | 'arpm';
}) {
  if (value === null) return <td className="products-table__num">—</td>;
  return (
    <td className="products-table__num">
      <span className="products-table__bar-wrap">
        <span
          className={`products-table__bar products-table__bar--${tone}`}
          style={{ width: max > 0 ? `${(value / max) * 100}%` : 0 }}
        />
        <span className="products-table__bar-value">{fmtRub(value)}</span>
      </span>
    </td>
  );
}

/**
 * Сводная таблица по курсам за период фильтра: покупки, оплаченные месяцы,
 * поступления, средняя цена покупки и средняя цена месяца.
 * Сумма столбца Revenue совпадает с плиткой Revenue выше.
 */
export function ProductsTable({ rows }: { rows: ProductRow[] }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'revenue', dir: 'desc' });

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = sortValue(a, sort.key);
      const bv = sortValue(b, sort.key);
      const cmp = typeof av === 'string' || typeof bv === 'string'
        ? String(av).localeCompare(String(bv), 'ru')
        : av - bv;
      return sort.dir === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [rows, sort]);

  // Максимум по каждому денежному столбцу — своя шкала полоски.
  const max = useMemo(() => ({
    revenue: rows.reduce((m, r) => Math.max(m, r.revenue), 0),
    asp: rows.reduce((m, r) => Math.max(m, r.asp ?? 0), 0),
    arpm: rows.reduce((m, r) => Math.max(m, r.arpm ?? 0), 0),
  }), [rows]);

  const totals = useMemo(() => ({
    purchases: rows.reduce((s, r) => s + r.purchases, 0),
    months: rows.reduce((s, r) => s + r.months, 0),
    revenue: rows.reduce((s, r) => s + r.revenue, 0),
  }), [rows]);

  const toggle = (key: SortKey) =>
    setSort((cur) => (cur.key === key
      ? { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: key === 'direction' ? 'asc' : 'desc' }));

  if (rows.length === 0) {
    return <div className="products-table__empty">За выбранный период оплат не было</div>;
  }

  return (
    <div className="data-table__scroll">
      <table className="data-table products-table">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                title={c.hint}
                aria-sort={sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                className={c.numeric ? 'products-table__num' : undefined}
              >
                <button type="button" className="products-table__sort" onClick={() => toggle(c.key)}>
                  {c.label}
                  <span className="products-table__arrow" aria-hidden="true">
                    {sort.key === c.key ? (sort.dir === 'asc' ? '↑' : '↓') : ''}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.direction_id ?? 'legacy'}>
              <td>{r.direction ?? <span className="products-table__legacy">{LEGACY}</span>}</td>
              <td className="products-table__num">{r.purchases}</td>
              <td className="products-table__num">{fmtMonths(r.months)}</td>
              <MoneyCell value={r.revenue} max={max.revenue} tone="revenue" />
              <MoneyCell value={r.asp} max={max.asp} tone="asp" />
              <MoneyCell value={r.arpm} max={max.arpm} tone="arpm" />
            </tr>
          ))}
          <tr className="products-table__total">
            <td>Итого</td>
            <td className="products-table__num">{totals.purchases}</td>
            <td className="products-table__num">{fmtMonths(totals.months)}</td>
            <td className="products-table__num">{fmtRub(totals.revenue)}</td>
            {/* Средние по школе не складываются из средних по курсам — считаем заново. */}
            <td className="products-table__num">
              {totals.purchases ? fmtRub(totals.revenue / totals.purchases) : '—'}
            </td>
            <td className="products-table__num">—</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
