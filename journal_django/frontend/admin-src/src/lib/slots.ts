import type { GroupScheduleSlot } from './types';

export const DOW = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'] as const;

export const MONTHS_RU = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
] as const;

export function formatSlot(s: Pick<GroupScheduleSlot, 'day_of_week' | 'start_time'>): string {
  const day = DOW[s.day_of_week] || '??';
  const time = String(s.start_time || '').slice(0, 5);
  return `${day} ${time}`;
}
