import { HelpPopover } from '../../components/ui/HelpPopover';
import { useState } from 'react';
import { SelectInput } from '../../components/form/SelectInput';
import { Checkbox } from '../../components/form/Checkbox';
import { dayMonth, parseIsoDate } from '../calendar/lib';
import { DOW_SHORT, WEEK_DOW, SLOT_COUNT, formatIntervals, intervalsToSlots, slotLabel, slotsToIntervals, totalHours, toMinutes } from './grid';
import type { Interval, ScheduleTemplate } from './types';
import { ScheduleDialog } from './ScheduleDialog';

export interface ScheduleSummaryProps {
  masks: boolean[][];
  dates: Date[] | null;
  todayIso: string;
  overrides: ScheduleTemplate['upcoming_overrides'];
  locked: boolean;
  overrideDates: Set<string>;
  onResetDay: (date: string) => void;
  onChangeDays: (updates: Record<number, boolean[]>) => void;
}
const TIME_OPTIONS = Array.from({ length: SLOT_COUNT + 1 }, (_, i) => ({ value: slotLabel(i), label: slotLabel(i) }));

export function ScheduleSummary({ masks, dates, todayIso, overrides, locked, overrideDates, onResetDay, onChangeDays }: ScheduleSummaryProps) {
  const [copyFrom, setCopyFrom] = useState<number | null>(null);
  const [targets, setTargets] = useState<number[]>([]);
  const dayLocked = (c: number) => locked || !!(dates && dates[c].toISOString().slice(0, 10) < todayIso);
  const dayLabel = (c: number) => `${DOW_SHORT[WEEK_DOW[c]]}${dates ? ` ${dayMonth(dates[c])}` : ''}`;
  const change = (c: number, ivs: Interval[]) => { if (!dayLocked(c)) onChangeDays({ [c]: intervalsToSlots(ivs) }); };
  const add = (c: number) => {
    const mask = [...masks[c]];
    // Предлагаем свободный отрезок с 09:00, затем ищем в начале суток.
    const order = [...Array.from({ length: 30 }, (_, i) => i + 18), ...Array.from({ length: 18 }, (_, i) => i)];
    const start = order.find((i) => !mask[i]);
    if (start === undefined) return;
    for (let i = start; i < Math.min(start + 4, SLOT_COUNT) && !mask[i]; i++) mask[i] = true;
    onChangeDays({ [c]: mask });
  };
  return (
    <aside className="sched-summary" aria-label="Рабочие интервалы по дням">
      <div className="sched-summary-heading"><div className="help-label"><h3>Рабочие часы</h3><HelpPopover label="Как редактировать интервалы">Шаг 30 минут. Интервалы можно менять здесь или в сетке.</HelpPopover></div><span>{totalHours(masks).toLocaleString('ru-RU')} ч{dates ? ' за неделю' : '/нед'}</span></div>
      <div className="sched-days">
        {WEEK_DOW.map((dow, c) => {
          const ivs = slotsToIntervals(masks[c]);
          const disabled = dayLocked(c);
          const iso = dates?.[c].toISOString().slice(0, 10);
          const changed = !!iso && overrideDates.has(iso);
          return <section className="sched-day" key={dow} aria-label={dayLabel(c)}>
            <div className="sched-day-head"><h4>{dayLabel(c)}</h4>
              {changed && <span className="sched-day-badge">Изменён</span>}
              {dates && dates[c].toISOString().slice(0, 10) < todayIso && <span className="sched-help">Только просмотр</span>}
              {!disabled && <button type="button" className="sched-link" onClick={() => { setTargets([]); setCopyFrom(c); }}>Копировать день</button>}
            </div>
            {ivs.length === 0 && <div className="sched-day-empty">Нерабочий день</div>}
            {ivs.map(([start, end], i) => <div className="sched-interval" key={i}>
              <SelectInput value={start} options={TIME_OPTIONS.filter((o) => toMinutes(o.value) < toMinutes(end))}
                aria-label={`${dayLabel(c)}, интервал ${i + 1}, начало`} searchable={false} disabled={disabled}
                onChange={(e) => change(c, ivs.map((iv, j) => j === i ? [e.target.value, end] : iv))} />
              <span aria-hidden="true">–</span>
              <SelectInput value={end} options={TIME_OPTIONS.filter((o) => toMinutes(o.value) > toMinutes(start))}
                aria-label={`${dayLabel(c)}, интервал ${i + 1}, конец`} searchable={false} disabled={disabled}
                onChange={(e) => change(c, ivs.map((iv, j) => j === i ? [start, e.target.value] : iv))} />
              {!disabled && <button type="button" className="sched-btn sched-icon-btn" aria-label={`Удалить интервал ${start}–${end}, ${dayLabel(c)}`}
                onClick={() => change(c, ivs.filter((_, j) => j !== i))}>×</button>}
            </div>)}
            {!disabled && <div className="sched-day-actions">
              <button type="button" className="sched-link sched-day-add" disabled={masks[c].every(Boolean)} onClick={() => add(c)}>+ Добавить интервал</button>
              {changed && <button type="button" className="sched-link" onClick={() => onResetDay(iso!)}>Вернуть обычный график</button>}
              {ivs.length > 0 && <button type="button" className="sched-link" onClick={() => change(c, [])}>Нерабочий день</button>}
            </div>}
          </section>;
        })}
      </div>
      <details className="sched-overrides">
        <summary>Будущие исключения · {overrides.length} <HelpPopover label="Как изменить исключение">Для редактирования выберите нужную неделю в режиме «Изменения по датам».</HelpPopover></summary>

        {overrides.length === 0 ? <p className="sched-help">Нет исключений</p> : <ul className="sched-sum-list">
          {overrides.map((o) => <li key={o.date}>{dayMonth(parseIsoDate(o.date))} · {o.intervals.length ? formatIntervals(o.intervals) : 'Нерабочий день'}</li>)}
        </ul>}
      </details>
      {copyFrom !== null && <ScheduleDialog title={`Копировать график: ${dayLabel(copyFrom)}`} busy={locked} onClose={() => setCopyFrom(null)}>
        <p>Интервалы выбранных дней будут заменены. Изменения применятся после сохранения графика.</p>
        <p className="sched-copy-preview">{formatIntervals(slotsToIntervals(masks[copyFrom]))}</p>
        <div className="sched-copy-days">
          {WEEK_DOW.map((dow, c) => c !== copyFrom && <Checkbox key={dow} label={dayLabel(c)} checked={targets.includes(c)} disabled={dayLocked(c)}
            onChange={(e) => setTargets((prev) => e.target.checked ? [...prev, c] : prev.filter((v) => v !== c))} />)}
        </div>
        <div className="sched-dialog-actions">
          <button type="button" className="sched-btn" disabled={locked} onClick={() => setCopyFrom(null)}>Отмена</button>
          <button type="button" className="sched-btn sched-btn--primary" disabled={locked || !targets.some((c) => !dayLocked(c))}
            onClick={() => {
              onChangeDays(Object.fromEntries(targets.filter((c) => !dayLocked(c)).map((c) => [c, [...masks[copyFrom]]])));
              setCopyFrom(null);
            }}>Применить к выбранным дням</button>
        </div>
      </ScheduleDialog>}
    </aside>
  );
}
