import { addMonths, monthLabel } from '../../lib/dates';
import { PageHead } from './PageHead';

interface Props {
  title: string;
  month: Date;
  currentMonth: Date;
  onChange: (month: Date) => void;
  isFetching?: boolean;
}

/**
 * Шапка экрана с переключателем месяцев: стрелки, подпись месяца и возврат к
 * текущему. Вперёд дальше текущего месяца не пускает — будущих уроков и выплат
 * нет. Общая для «Зарплаты» и «Моих уроков».
 */
export function MonthNav({ title, month, currentMonth, onChange, isFetching }: Props) {
  const isCurrentMonth = month.getTime() >= currentMonth.getTime();
  return (
    <PageHead title={title} sub={isFetching ? <span className="t-updating">обновление…</span> : undefined}>
      <div className="cal-week-nav">
        <button
          type="button"
          className="cal-nav-btn"
          onClick={() => onChange(addMonths(month, -1))}
          aria-label="Предыдущий месяц"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
        </button>
        <span className="cal-week-label">{monthLabel(month)}</span>
        <button
          type="button"
          className="cal-nav-btn"
          onClick={() => onChange(addMonths(month, 1))}
          disabled={isCurrentMonth}
          aria-label="Следующий месяц"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
        </button>
        {!isCurrentMonth && (
          <button type="button" className="cal-today-btn" onClick={() => onChange(currentMonth)}>
            Текущий месяц
          </button>
        )}
      </div>
    </PageHead>
  );
}
