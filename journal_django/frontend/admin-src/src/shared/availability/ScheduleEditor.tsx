import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, api, extractErrorDetail } from '../../lib/api';
import type { CalendarResponse } from '../calendar/types';
import { Modal } from '../calendar/Modal';
import { addDays, currentMondayMsk, dayMonth, isoDate, todayMsk } from '../calendar/lib';
import {
  SLOT_COUNT, WEEK_DOW, intervalsToSlots, sameSlots, slotsToIntervals,
} from './grid';
import { useScheduleMutations, useScheduleRange, useScheduleTemplate } from './useSchedule';
import { ScheduleGrid } from './ScheduleGrid';
import { ScheduleSummary } from './ScheduleSummary';
import './schedule.css';

export interface ScheduleEditorProps {
  /** '/api/work-schedule' | `/api/admin/teachers/${id}/work-schedule` */
  basePath: string;
  /** URL календаря для подсказки-уроков недели: (from, to) => string. */
  calendarUrl: (from: string, to: string) => string;
  /** Архивный преподаватель / нет прав — только просмотр. */
  readOnly?: boolean;
}

type Mode = 'template' | 'week';

const EMPTY_DAY = (): boolean[] => Array<boolean>(SLOT_COUNT).fill(false);

function errText(err: unknown): string {
  if (err instanceof ApiError) return extractErrorDetail(err.details) ?? err.message;
  return 'Не удалось сохранить. Попробуйте ещё раз.';
}

export function ScheduleEditor({ basePath, calendarUrl, readOnly = false }: ScheduleEditorProps) {
  const [mode, setMode] = useState<Mode>('template');
  const [monday, setMonday] = useState<Date>(() => currentMondayMsk());
  const [draft, setDraft] = useState<boolean[][] | null>(null);
  // Отложенный переход, ждущий ответа в окне «Сбросить изменения?».
  const [pendingNav, setPendingNav] = useState<(() => void) | null>(null);

  const todayIso = isoDate(todayMsk());
  const dates = useMemo(() => WEEK_DOW.map((_, c) => addDays(monday, c)), [monday]);
  const isoDates = useMemo(() => dates.map(isoDate), [dates]);
  const from = isoDates[0];
  const to = isoDates[6];

  const tpl = useScheduleTemplate(basePath);
  const range = useScheduleRange(basePath, from, to, mode === 'week');
  const { saveTemplate, saveWeek, clearOverride } = useScheduleMutations(basePath);

  const lessonsQ = useQuery({
    queryKey: ['schedule-lessons', calendarUrl(from, to)],
    queryFn: () => api<CalendarResponse>('GET', calendarUrl(from, to)),
    enabled: mode === 'week',
  });

  // Смена преподавателя без ремоунта страницы: черновик чужого графика не должен уцелеть.
  useEffect(() => { setDraft(null); }, [basePath]);

  const active = mode === 'template' ? tpl : range;
  // Placeholder — это данные прошлой недели/преподавателя: показываем, но не даём править.
  const loading = !active.data || (mode === 'week' && range.isPlaceholderData);

  const base: boolean[][] = useMemo(() => {
    if (mode === 'template') {
      return WEEK_DOW.map((dow) => intervalsToSlots(tpl.data?.days[String(dow)] ?? []));
    }
    return isoDates.map((iso) => (range.data?.windows[iso] ? intervalsToSlots(range.data.windows[iso]) : EMPTY_DAY()));
  }, [mode, tpl.data, range.data, isoDates]);

  const masks = draft ?? base;
  const dirty = draft != null && draft.some((m, c) => !sameSlots(m, base[c]));
  const pending = saveTemplate.isPending || saveWeek.isPending || clearOverride.isPending;
  // Во время сохранения правки запрещены: иначе они уйдут в уже отправленный запрос и потеряются.
  const locked = readOnly || loading || pending;

  useEffect(() => {
    if (!dirty) return;
    // returnValue нужен старым браузерам: без него диалог подтверждения не показывается.
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);

  const guard = (fn: () => void) => {
    if (dirty) setPendingNav(() => fn);
    else fn();
  };

  // Ошибка на экране — одна из трёх мутаций (`??`), поэтому перед каждым действием
  // гасим ошибки всех: иначе старая ошибка переживает следующее успешное действие.
  const resetErrors = () => { saveTemplate.reset(); saveWeek.reset(); clearOverride.reset(); };
  const clearDay = (d: string) => { resetErrors(); clearOverride.mutate(d); };

  const onSet = (c: number, i: number, value: boolean) => {
    if (locked) return;
    setDraft((prev) => {
      const cur = prev ?? base;
      if (cur[c][i] === value) return prev;
      return cur.map((m, k) => (k === c ? m.map((v, j) => (j === i ? value : v)) : m));
    });
  };

  const save = () => {
    if (!draft) return;
    const done = { onSuccess: () => setDraft(null) };
    resetErrors();
    if (mode === 'template') {
      saveTemplate.mutate(
        Object.fromEntries(WEEK_DOW.map((dow, c) => [String(dow), slotsToIntervals(draft[c])])),
        done,
      );
    } else {
      const days: Record<string, ReturnType<typeof slotsToIntervals>> = {};
      isoDates.forEach((iso, c) => { if (iso >= todayIso) days[iso] = slotsToIntervals(draft[c]); });
      saveWeek.mutate({ monday: isoDate(monday), days }, done);
    }
  };

  const error = saveTemplate.error ?? saveWeek.error ?? clearOverride.error;
  const overrideDates = useMemo(() => new Set(range.data?.override_dates ?? []), [range.data]);
  const lessons = (mode === 'week' ? lessonsQ.data?.occurrences : undefined) ?? [];

  const switchMode = (m: Mode) => {
    if (m === mode) return;
    guard(() => { setDraft(null); resetErrors(); setMode(m); });
  };
  const shiftWeek = (n: number) => guard(() => { setDraft(null); resetErrors(); setMonday((d) => addDays(d, n * 7)); });

  // Ошибка не должна убирать панель управления: режим и недели остаются рабочими.
  // Ошибка фонового рефетча при уже загруженных данных не прячет сетку (TanStack v5: isError + data).
  const loadFailed = (tpl.isError && !tpl.data) || (mode === 'week' && range.isError && !range.data);
  const ready = !loading && !!tpl.data;

  return (
    <div className="sched">
      {readOnly && <div className="sched-note">График только для просмотра</div>}

      <div className="sched-bar">
        <div className="sched-seg">
          <button type="button" aria-pressed={mode === 'template'} disabled={pending} onClick={() => switchMode('template')}>
            Шаблон недели
          </button>
          <button type="button" aria-pressed={mode === 'week'} disabled={pending} onClick={() => switchMode('week')}>
            Конкретная неделя
          </button>
        </div>
        {mode === 'week' && (
          <div className="sched-weeknav">
            <button type="button" className="sched-btn" aria-label="Предыдущая неделя" disabled={pending} onClick={() => shiftWeek(-1)}>‹</button>
            <span className="sched-weeklabel">{dayMonth(monday)}–{dayMonth(addDays(monday, 6))}</span>
            <button type="button" className="sched-btn" aria-label="Следующая неделя" disabled={pending} onClick={() => shiftWeek(1)}>›</button>
          </div>
        )}
        {dirty && (
          <div className="sched-actions">
            <button type="button" className="sched-btn sched-btn--primary" disabled={pending} onClick={save}>
              Сохранить
            </button>
            <button type="button" className="sched-btn" disabled={pending} onClick={() => { setDraft(null); resetErrors(); }}>
              Отменить
            </button>
          </div>
        )}
      </div>
      {error && <div className="sched-error" role="alert">{errText(error)}</div>}

      {loadFailed ? (
        <div className="sched-state">Не удалось загрузить график. Обновите страницу.</div>
      ) : (
        <div className="sched-layout">
          {/* Скелет, пока нет данных именно этой недели: данные прошлой недели дали бы ложное «выходной». */}
          {loading ? (
            <div className="sched-skeleton" aria-busy="true" />
          ) : (
            <ScheduleGrid
              masks={masks}
              dates={mode === 'week' ? dates : null}
              isoDates={mode === 'week' ? isoDates : null}
              todayIso={todayIso}
              overrideDates={overrideDates}
              lessons={lessons}
              locked={locked}
              canClear={!readOnly}
              dirty={dirty}
              busy={pending}
              onSet={onSet}
              onClearDay={clearDay}
            />
          )}
          {ready ? (
            <ScheduleSummary
              masks={masks}
              overrides={tpl.data!.upcoming_overrides}
              canEdit={!readOnly}
              busy={pending}
              dirty={dirty}
              onRemoveOverride={clearDay}
            />
          ) : (
            <div className="sched-skeleton sched-skeleton--side" aria-busy="true" />
          )}
        </div>
      )}

      {pendingNav && (
        <Modal title="Несохранённые изменения" onClose={() => setPendingNav(null)}>
          <div>Есть несохранённые изменения. Сбросить их?</div>
          <div className="sched-actions">
            <button
              type="button"
              className="sched-btn sched-btn--primary"
              onClick={() => { const fn = pendingNav; setPendingNav(null); fn(); }}
            >
              Сбросить
            </button>
            <button type="button" className="sched-btn" onClick={() => setPendingNav(null)}>
              Остаться
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
