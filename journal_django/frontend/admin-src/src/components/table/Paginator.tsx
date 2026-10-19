import { SelectInput } from '../form/SelectInput';

interface PaginatorProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];

export function Paginator({ page, pageSize, total, onPageChange, onPageSizeChange }: PaginatorProps) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const pages: (number | 'gap')[] = [];
  const visible = new Set<number>();
  visible.add(1);
  visible.add(lastPage);
  for (let i = page - 2; i <= page + 2; i++) {
    if (i >= 1 && i <= lastPage) visible.add(i);
  }
  const sorted = Array.from(visible).sort((a, b) => a - b);
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i] - sorted[i - 1] > 1) pages.push('gap');
    pages.push(sorted[i]);
  }

  return (
    <div className="paginator">
      <div className="paginator__pages">
        <button
          type="button"
          className="paginator__btn"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          aria-label="Предыдущая страница"
        >«</button>
        {pages.map((p, idx) =>
          p === 'gap' ? (
            <span key={`gap-${idx}`} className="paginator__gap">…</span>
          ) : (
            <button
              key={p}
              type="button"
              className={`paginator__btn${p === page ? ' is-current' : ''}`}
              onClick={() => onPageChange(p)}
            >{p}</button>
          ),
        )}
        <button
          type="button"
          className="paginator__btn"
          onClick={() => onPageChange(Math.min(lastPage, page + 1))}
          disabled={page >= lastPage}
          aria-label="Следующая страница"
        >»</button>
      </div>
      <div className="paginator__info">
        {from}-{to} из {total}
      </div>
      <div className="paginator__size">
        На странице:
        <SelectInput
          className="paginator__size-select"
          value={String(pageSize)}
          onChange={(e) => { onPageSizeChange(Number(e.target.value)); }}
          options={PAGE_SIZE_OPTIONS.map((s) => ({ value: s, label: String(s) }))}
        />
      </div>
    </div>
  );
}
