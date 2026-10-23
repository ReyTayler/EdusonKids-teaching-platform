import type { PlanRow } from '../../hooks/useGroupPlanCalendar';
import { fmtDate } from '../../lib/format';

/** Подпись строки плана в списках выбора занятия: «Урок 5 — 25.10.2026 18:00». */
export function lessonLabel(r: PlanRow): string {
  const time = r.scheduled_time ? r.scheduled_time.slice(0, 5) : '';
  const pos = r.seq != null ? `Урок ${r.seq}` : 'Доп. занятие';
  return `${pos} — ${fmtDate(r.scheduled_date)}${time ? ` ${time}` : ''}`;
}
