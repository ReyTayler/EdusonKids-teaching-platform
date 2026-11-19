import type { Occurrence } from './types';
import { toMinutes } from '../availability/grid';

/** Минимальная высота блока — 45-минутный урок остаётся читаемым (время+название). */
export const MIN_BLOCK_H = 26;

export interface Laid {
  occ: Occurrence;
  key: string;
  top: number;
  height: number;
  lane: number;
  lanes: number;
}

/** 'HH:MM' + длительность (мин) → время окончания 'HH:MM' (диапазон в блоке). */
export function endTime(time: string, minutes: number): string {
  const [h, m] = time.split(':').map(Number);
  const total = h * 60 + m + minutes;
  const hh = Math.floor(total / 60) % 24;
  const mm = total % 60;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

/**
 * Раскладка одной колонки (день недели или преподаватель внутри дня): высота
 * блока — из durationMinutes (фолбэк 60, admin /plan поле может не отдавать),
 * пересекающиеся занятия делят ширину колонки поровну «дорожками» (как в Google
 * Calendar): кластер пересечений → жадное назначение первой свободной дорожки
 * в порядке начала занятия.
 *
 * Общая для WeekGrid и TeacherWeekGrid, чтобы не копировать алгоритм.
 * startMin — минута суток, с которой начинается колонка (0 у суточной сетки;
 * у TeacherWeekGrid — первый час дня), rowH — высота часа в px.
 */
export function layoutColumn(
  occs: Occurrence[],
  bodyHeight: number,
  rowH: number,
  startMin = 0,
): Laid[] {
  const items: Laid[] = occs
    .map((occ, i) => {
      const rawTop = Math.max(0, ((toMinutes(occ.time!) - startMin) / 60) * rowH);
      const minutes = occ.durationMinutes ?? 60;
      const raw = (minutes / 60) * rowH - 4;
      const height = Math.max(MIN_BLOCK_H, Math.min(raw, bodyHeight - rawTop - 4));
      // Урок у полуночи (23:50 и т.п.): MIN_BLOCK_H мог бы выпихнуть блок ниже
      // сетки — поднимаем top, чтобы top+height не выходил за низ колонки.
      const top = Math.min(rawTop, bodyHeight - height - 2);
      return { occ, key: `${occ.group}-${occ.date}-${occ.time}-${i}`, top, height, lane: 0, lanes: 1 };
    })
    .sort((a, b) => a.top - b.top || b.height - a.height);

  let cluster: Laid[] = [];
  let clusterEnd = -Infinity;

  const flush = () => {
    const laneEnds: number[] = []; // нижняя граница последнего занятия каждой дорожки
    for (const it of cluster) {
      let lane = laneEnds.findIndex((end) => end <= it.top);
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(0);
      }
      it.lane = lane;
      laneEnds[lane] = it.top + it.height;
    }
    for (const it of cluster) it.lanes = laneEnds.length;
    cluster = [];
    clusterEnd = -Infinity;
  };

  for (const it of items) {
    if (cluster.length > 0 && it.top >= clusterEnd) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it.top + it.height);
  }
  if (cluster.length > 0) flush();

  return items;
}

/** Минуты от полуночи ТЕКУЩЕГО времени в МСК (для линии «сейчас»). */
// Форматтер создаётся один раз: mskNowMinutes — getSnapshot useSyncExternalStore,
// React зовёт его на каждом рендере каждой линии «сейчас».
const MSK_HHMM = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit', hour12: false,
});

export function mskNowMinutes(): number {
  const hhmm = MSK_HHMM.format(new Date());
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}
