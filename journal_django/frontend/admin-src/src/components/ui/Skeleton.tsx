/**
 * `roomy` — тот же признак, что и у DataTable: скелетон обязан держать шаг
 * строки той таблицы, которую замещает, иначе при появлении данных вёрстка
 * прыгает с 36px строки на 52px. Панель (белый фон + тень) рисуется только в
 * просторном режиме — в плотном скелетон исторически показывался «голым», и
 * менять это здесь не место.
 */
export function TableSkeleton({ rows = 5, cols = 5, roomy }: { rows?: number; cols?: number; roomy?: boolean }) {
  const panel = roomy ? 'table-panel table-panel--roomy' : '';
  return (
    <div className={panel}>
    <div className="table-wrap" aria-busy="true">
      <span className="sr-only">Загружаем данные…</span>
      <table>
        <tbody>
          {Array.from({ length: rows }).map((_, i) => (
            <tr key={i}>
              {Array.from({ length: cols }).map((_, j) => (
                <td key={j}><div className="skeleton-cell" /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </div>
  );
}

/**
 * Загрузка целой страницы. Раньше здесь была строка «Загружаем…» по центру:
 * она не давала понять, что откроется, и при появлении данных вёрстка прыгала
 * с одной строки на полный экран. Скелетон держит примерную форму страницы —
 * заголовок, ряд плиток, список — и высоту вместе с ней.
 *
 * Форма нейтральная: PageLoading используется и списками, и карточками сущностей.
 */
export function PageLoading() {
  return (
    <div className="page-skeleton" aria-busy="true" aria-live="polite">
      <span className="sr-only">Загружаем…</span>

      <div className="page-skeleton__head">
        <div className="skeleton-block sk-w-25 sk-h-20" />
        <div className="skeleton-block sk-w-40" />
      </div>

      <div className="page-skeleton__tiles">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="page-skeleton__tile" />
        ))}
      </div>

      <div className="page-skeleton__rows">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton-row">
            <div className="skeleton-block skeleton-avatar" />
            <div className="skeleton-block skeleton-text-md" />
            <div className="skeleton-block skeleton-text-sm" />
            <div className="skeleton-block skeleton-text-sm" />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Загрузка блока внутри вкладки или карточки — там, где полноэкранный скелетон
 * избыточен, а текст «Загружаем…» так же схлопывает высоту до одной строки.
 */
export function BlockLoading({ rows = 3, label = 'Загружаем…' }: { rows?: number; label?: string }) {
  return (
    <div className="block-skeleton" aria-busy="true" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton-block sk-h-20" />
      ))}
    </div>
  );
}
