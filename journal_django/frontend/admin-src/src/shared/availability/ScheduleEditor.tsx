import { HelpPopover } from '../../components/ui/HelpPopover';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useBlocker } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ApiError, api, extractErrorDetail } from '../../lib/api';
import type { CalendarResponse } from '../calendar/types';
import { addDays, currentMondayMsk, isoDate, todayMsk, weekRangeLabel } from '../calendar/lib';
import { WEEK_DOW, intervalsToSlots, sameSlots, slotsToIntervals } from './grid';
import type { Interval } from './types';
import { useScheduleMutations, useScheduleRange, useScheduleTemplate } from './useSchedule';
import { ScheduleGrid } from './ScheduleGrid';
import { ScheduleSummary } from './ScheduleSummary';
import { ScheduleDialog } from './ScheduleDialog';
import './schedule.css';

export interface ScheduleEditorProps {
  basePath: string;
  calendarUrl: (from: string, to: string) => string;
  readOnly?: boolean;
  readOnlyReason?: string;
  contextTitle?: string;
}
type Mode = 'template' | 'week';
// Фоновый refetch не меняет снимок, относительно которого редактируется график.
type Draft = { original: boolean[][]; masks: boolean[][]; resets: string[] };
function errText(err: unknown): string {
  if (err instanceof ApiError) return extractErrorDetail(err.details) ?? err.message;
  return 'Не удалось сохранить. Попробуйте ещё раз.';
}

export function ScheduleEditor({ basePath, calendarUrl, readOnly = false, readOnlyReason, contextTitle = 'График работы' }: ScheduleEditorProps) {
  const [mode, setMode] = useState<Mode>('template');
  const [monday, setMonday] = useState(currentMondayMsk);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pendingNav, setPendingNav] = useState<(() => void) | null>(null);
  const [saved, setSaved] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const [todayIso, setTodayIso] = useState(() => isoDate(todayMsk()));
  useEffect(() => {
    const t = setInterval(() => setTodayIso(isoDate(todayMsk())), 60_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!expanded) return;
    const before = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Полноэкранная область не должна оставлять доступный с Tab фон.
    const siblings: { el: HTMLElement; inert: boolean }[] = [];
    let node: HTMLElement | null = rootRef.current;
    while (node && node !== document.body) {
      for (const sibling of node.parentElement?.children ?? []) {
        if (sibling !== node && sibling instanceof HTMLElement) {
          siblings.push({ el: sibling, inert: sibling.inert });
          sibling.inert = true;
        }
      }
      node = node.parentElement;
    }
    expandButton.current?.focus();
    return () => {
      document.body.style.overflow = before;
      siblings.forEach(({ el, inert }) => { el.inert = inert; });
    };
  }, [expanded]);

  const dates = useMemo(() => WEEK_DOW.map((_, c) => addDays(monday, c)), [monday]);
  const isoDates = useMemo(() => dates.map(isoDate), [dates]);
  const from = isoDates[0], to = isoDates[6];
  const tpl = useScheduleTemplate(basePath);
  const range = useScheduleRange(basePath, from, to, mode === 'week');
  const { saveTemplate, saveWeek } = useScheduleMutations(basePath);
  const lessonsQ = useQuery({
    queryKey: ['schedule-lessons', calendarUrl(from, to)],
    queryFn: () => api<CalendarResponse>('GET', calendarUrl(from, to)),
    enabled: mode === 'week',
  });
  useEffect(() => { setDraft(null); setSaved(false); }, [basePath]);
  const loading = !tpl.data || (mode === 'week' && (!range.data || range.isPlaceholderData));
  const base = useMemo(() => WEEK_DOW.map((dow, c) => intervalsToSlots(
    mode === 'template' ? tpl.data?.days[String(dow)] ?? [] : range.data?.windows[isoDates[c]] ?? [],
  )), [mode, tpl.data, range.data, isoDates]);
  const masks = draft?.masks ?? base;
  const changedColumns = useMemo(() => WEEK_DOW.map((_, c) => c).filter((c) => draft && (
    !sameSlots(draft.masks[c], draft.original[c]) || draft.resets.includes(isoDates[c])
  )), [draft, isoDates]);
  const dirty = changedColumns.length > 0;
  const pending = saveTemplate.isPending || saveWeek.isPending;
  const locked = readOnly || loading || pending;
  const isDayLocked = (c: number) => locked || (mode === 'week' && isoDates[c] < todayIso);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => (dirty || pending) && (
    currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search
  ));
  useEffect(() => {
    if (!dirty && !pending) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty, pending]);
  const resetErrors = () => { if (saveTemplate.isError) saveTemplate.reset(); if (saveWeek.isError) saveWeek.reset(); };
  const discard = () => { setDraft(null); setSaved(false); resetErrors(); };
  const guard = (fn: () => void) => {
    if (pending) return;
    if (dirty) setPendingNav(() => fn);
    else { discard(); fn(); }
  };
  const changeDays = (updates: Record<number, boolean[]>, resetDate?: string) => {
    setSaved(false); resetErrors();
    setDraft((prev) => {
      const cur = prev ?? { original: base, masks: base, resets: [] };
      return {
        ...cur,
        masks: cur.masks.map((m, c) => isDayLocked(c) ? m : updates[c] ?? m),
        resets: resetDate ? [...new Set([...cur.resets, resetDate])] : cur.resets,
      };
    });
  };
  const clearDay = (date: string) => {
    const c = isoDates.indexOf(date);
    if (c < 0 || isDayLocked(c)) return;
    changeDays({ [c]: intervalsToSlots(tpl.data?.days[String(WEEK_DOW[c])] ?? []) }, date);
  };
  const save = async (): Promise<boolean> => {
    if (!dirty || !draft || pending || readOnly) return false;
    resetErrors();
    try {
      if (mode === 'template') {
        await saveTemplate.mutateAsync(Object.fromEntries(WEEK_DOW.map((dow, c) => [String(dow), slotsToIntervals(masks[c])])));
      } else {
        // Не перезаписываем соседние дни. Если дата успела стать прошлой,
        // сервер вернёт ошибку: её черновик нельзя молча отбросить.
        const days: Record<string, Interval[]> = {};
        for (const c of changedColumns) days[isoDates[c]] = slotsToIntervals(masks[c]);
        await saveWeek.mutateAsync({ monday: from, days });
      }
      setDraft(null); setSaved(true);
      return true;
    } catch { return false; }
  };
  const error = saveTemplate.error ?? saveWeek.error;
  const overrideDates = useMemo(() => {
    const result = new Set(range.data?.override_dates ?? []);
    if (draft && mode === 'week') WEEK_DOW.forEach((dow, c) => {
      if (!changedColumns.includes(c)) return;
      if (sameSlots(masks[c], intervalsToSlots(tpl.data?.days[String(dow)] ?? []))) result.delete(isoDates[c]);
      else result.add(isoDates[c]);
    });
    return result;
  }, [range.data, draft, mode, masks, tpl.data, isoDates, changedColumns]);
  const loadFailed = (tpl.isError && !tpl.data) || (mode === 'week' && range.isError && !range.data);
  const noSchedule = !loading && (mode === 'template'
    ? !Object.values(tpl.data?.days ?? {}).some((ivs) => ivs.length) && !tpl.data?.upcoming_overrides.length
    : !range.data?.has_schedule);
  const leave = () => {
    const fn = pendingNav;
    setPendingNav(null);
    if (blocker.state === 'blocked') blocker.proceed();
    else { discard(); fn?.(); }
  };
  const saveLabel = mode === 'template' ? 'Сохранить обычный график' : 'Сохранить изменения недели';

  return (
    <div className={`sched${expanded ? ' sched--expanded' : ''}`} ref={rootRef}
      role="region" aria-label="Редактор графика работы"
      onKeyDown={(e) => {
        if (expanded && e.key === 'Escape' && !(e.target as HTMLElement).closest('[role="dialog"]')) {
          e.preventDefault(); setExpanded(false); expandButton.current?.focus();
        }
      }}>
      {readOnly && <div className="sched-note">{readOnlyReason ?? 'У вас нет прав на редактирование графика. Доступен только просмотр.'}</div>}
      {expanded && <h2 className="sched-expanded-title">{contextTitle}</h2>}
      <div className="sched-bar">
        <div className="sched-seg" role="group" aria-label="Область действия графика">
          <button type="button" aria-pressed={mode === 'template'} disabled={pending}
            onClick={() => { if (mode !== 'template') guard(() => setMode('template')); }}>Обычный график</button>
          <button type="button" aria-pressed={mode === 'week'} disabled={pending}
            onClick={() => { if (mode !== 'week') guard(() => setMode('week')); }}>Изменения по датам</button>
        </div>
        <span className="sched-timezone">Время по Москве · UTC+3</span>
        <HelpPopover label="Как работать с графиком">
          <p>{mode === 'template'
          ? 'Повторяется каждую неделю. Для отдельных дат могут действовать изменения.'
          : 'Изменения применяются только к выбранным датам. Остальные дни следуют обычному графику.'}</p>
          <p>График показывает доступность преподавателя и не запрещает назначение уроков вне рабочих часов.
        {mode === 'week' && ' Назначенные уроки отображаются отдельно.'}</p>
          <p>Выделяйте рабочие часы мышью. С клавиатуры: стрелки — перемещение, Space или Enter — изменить ячейку.</p>
        </HelpPopover>
        <button ref={expandButton} type="button" className="sched-btn sched-expand" aria-pressed={expanded}
          onClick={() => setExpanded((v) => !v)}>{expanded ? 'Свернуть график' : 'Развернуть график'}</button>
      </div>
      <div className="sched-context">
        {mode === 'week' && <div className="sched-weeknav">
          <button type="button" className="sched-btn" aria-label="Предыдущая неделя" disabled={pending}
            onClick={() => guard(() => setMonday((d) => addDays(d, -7)))}>‹</button>
          <span className="sched-weeklabel">{weekRangeLabel(monday)}</span>
          <button type="button" className="sched-btn" aria-label="Следующая неделя" disabled={pending}
            onClick={() => guard(() => setMonday((d) => addDays(d, 7)))}>›</button>
        </div>}
      </div>
      <div className="sched-legend" aria-label="Обозначения">
        <span><i className="sched-key sched-key--open" />Рабочее время</span>
        <span><i className="sched-key sched-key--closed" />Нерабочее время</span>
        {mode === 'week' && <span><i className="sched-key sched-key--lesson" />Назначенный урок</span>}
      </div>

      {error && <div className="sched-error" role="alert">{errText(error)} Черновик сохранён на этой странице.</div>}
      {loadFailed ? <div className="sched-state" role="alert">
        Не удалось загрузить график.
        <button type="button" className="sched-btn" onClick={() => { void tpl.refetch(); if (mode === 'week') void range.refetch(); }}>Повторить загрузку</button>
      </div> : <>
        {noSchedule && !dirty && <div className="sched-note">
          <strong>График пока не задан.</strong> <HelpPopover label="Как задать график">{readOnly ? 'Рабочие часы ещё не указаны.' : 'Добавьте рабочие часы в списке дней или выделите их в сетке.'}</HelpPopover>
          {!readOnly && <button type="button" className="sched-btn" onClick={() => rootRef.current?.querySelector<HTMLButtonElement>('.sched-day-add')?.focus()}>Добавить рабочие часы</button>}
        </div>}
        {mode === 'week' && lessonsQ.isError && <div className="sched-note" role="status">
          Не удалось показать занятия. График доступен для работы.
          <button type="button" className="sched-btn" disabled={lessonsQ.isFetching} onClick={() => void lessonsQ.refetch()}>Повторить загрузку занятий</button>
        </div>}
        {mode === 'week' && lessonsQ.isPending && <div className="sched-help" role="status">Загружаем занятия…</div>}
        {mode === 'week' && to < todayIso && <div className="sched-note">Прошедшая неделя · только просмотр</div>}
        <div className="sched-layout">
          {loading ? <div className="sched-skeleton" aria-busy="true" aria-label="Загрузка графика" /> :
            <div className="sched-grid-panel">

              <ScheduleGrid masks={masks} dates={mode === 'week' ? dates : null} isoDates={mode === 'week' ? isoDates : null}
                todayIso={todayIso} overrideDates={overrideDates} lessons={mode === 'week' ? lessonsQ.data?.occurrences ?? [] : []}
                locked={locked} canClear={!readOnly} busy={pending} onClearDay={clearDay}
                onSet={(c, i, value) => {
                  if (isDayLocked(c)) return;
                  setSaved(false); resetErrors();
                  setDraft((prev) => {
                    const cur = prev ?? { original: base, masks: base, resets: [] };
                    return { ...cur, masks: cur.masks.map((m, k) => k === c ? m.map((v, j) => j === i ? value : v) : m) };
                  });
                }} />
            </div>}
          {!loading ? <ScheduleSummary masks={masks} dates={mode === 'week' ? dates : null} todayIso={todayIso}
            overrides={tpl.data!.upcoming_overrides} locked={locked} overrideDates={overrideDates} onResetDay={clearDay}
            onChangeDays={(updates) => { if (!locked) changeDays(updates); }} /> :
            <div className="sched-skeleton sched-skeleton--side" aria-busy="true" />}
        </div>
      </>}
      <div className="sched-savebar" aria-busy={pending}>
        <span role="status" aria-live="polite">{pending ? 'Сохраняем…' : dirty ? `Изменено дней: ${changedColumns.length}` : saved ? 'График сохранён' : 'Нет несохранённых изменений'}</span>
        {dirty && <div className="sched-actions">
          <button type="button" className="sched-btn" disabled={pending} onClick={discard}>Отменить изменения</button>
          <button type="button" className="sched-btn sched-btn--primary" disabled={pending} onClick={() => void save()}>{pending ? 'Сохраняем…' : saveLabel}</button>
        </div>}
      </div>
      {(pendingNav || blocker.state === 'blocked') && <ScheduleDialog title="Несохранённые изменения" busy={pending}
        onClose={() => { setPendingNav(null); if (blocker.state === 'blocked') blocker.reset(); }}>
        <p>Сохранить изменения графика перед переходом?</p>
        {error && <p className="sched-error" role="alert">{errText(error)}</p>}
        <div className="sched-dialog-actions">
          <button type="button" className="sched-btn" disabled={pending} onClick={() => { setPendingNav(null); if (blocker.state === 'blocked') blocker.reset(); }}>Остаться</button>
          <button type="button" className="sched-btn" disabled={pending} onClick={leave}>Уйти без сохранения</button>
          <button type="button" className="sched-btn sched-btn--primary" disabled={pending || !dirty}
            onClick={async () => { if (await save()) leave(); }}>{pending ? 'Сохраняем…' : 'Сохранить и перейти'}</button>
        </div>
      </ScheduleDialog>}
    </div>
  );
}
