import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, fetchAllPages } from '../lib/api';
import type { Group } from '../lib/types';

/**
 * ЕДИНИЦЫ. Бэкенд намеренно разводит два слова, не смешивать:
 *
 *   sessions   — ЗАНЯТИЯ, штуки. 45-минутное занятие = 1. Это нагрузка препода.
 *   lessons_*  — УРОКИ курса, с весом. 45-минутное занятие = 0.5. Это прогресс.
 *
 * Смешение этих единиц уже давало баг в статистике ученика, поэтому в ответе
 * слово «lessons» встречается только там, где вес применён.
 */
export interface TeacherDirectionStat {
  direction_id: number;
  name: string;
  color: string | null;
  sessions: number;
  minutes: number;
}

export interface TeacherGroupProgress {
  group_id: number;
  /** numeric(6,1) из PG приходит СТРОКОЙ ('2.0') — приводить Number() на месте использования. */
  lessons_done: string | number;
  /**
   * null — длина курса не задана нигде; 0 — задана нулём в направлении
   * (CHECK там допускает >= 0). Оба случая означают «длины курса нет»:
   * делить без проверки нельзя, получите Infinity.
   */
  lessons_total: number | null;
}

export interface TeacherStats {
  month: string;
  /** Год выбранного месяца — им подписан график и по нему построен `monthly`. */
  year: number;
  last_lesson_date: string | null;
  total: { sessions: number; minutes: number; substitutions: number };
  by_direction: TeacherDirectionStat[];
  by_duration: { minutes: number; sessions: number }[];
  /** Январь–декабрь года `year`, всегда 12 точек, пустые месяцы нулями. */
  monthly: { month: string; sessions: number }[];
  group_progress: TeacherGroupProgress[];
  /** `pct` — null, когда считать не из чего: 0% и «занятий не было» на экране
   *  выглядят одинаково, а значат противоположное. */
  attendance: { present: number; counted: number; pct: number | null };
  /** Все 7 дней, Вс=0 — как в `DOW` из lib/slots. */
  weekday_load: { day: number; sessions: number }[];
  /** СЕЙЧАС, не за месяц: просрочка не перестаёт быть просрочкой от смены периода. */
  unfilled: { count: number; oldest_date: string | null };
  absences: {
    registered: number;
    makeup_done: number;
    makeup_scheduled: number;
    burned: number;
    /** Очередь «ждут решения» — тоже сейчас, а не за месяц. */
    pending_now: number;
  };
  /**
   * Приходит ТОЛЬКО суперадмину: раздел «Зарплата» закрыт `IsSuperAdmin`, а
   * карточку видит и менеджер. Ключ отсутствует, а не приходит нулями —
   * «0 ₽» читалось бы как «не заплатили».
   */
  payroll?: { payment: string; penalty: string };
}

/**
 * Показатели преподавателя за месяц.
 *
 * keepPreviousData обязателен: без него переключение месяца ◀ ▶ схлопывает
 * плитки в скелет на каждый клик (правило всех параметризованных хуков проекта).
 */
export function useTeacherStats(teacherId: number, month: string) {
  return useQuery({
    queryKey: ['teacher-stats', teacherId, month],
    queryFn: () =>
      api<TeacherStats>('GET', `/api/admin/teachers/${teacherId}/stats?month=${month}`),
    enabled: Number.isFinite(teacherId) && teacherId > 0,
    placeholderData: keepPreviousData,
  });
}

/**
 * Группы одного преподавателя, включая архивные.
 *
 * Отдельного эндпоинта нет и не нужно: список групп уже принимает
 * filter[teacher_id] и отдаёт members_count, направление и слоты. Раньше
 * страница тянула useGroupsAll(true) — ВСЕ группы школы — и фильтровала
 * на клиенте.
 */
export function useTeacherGroups(teacherId: number) {
  return useQuery({
    queryKey: ['teacher-groups', teacherId],
    queryFn: () => {
      const qs = new URLSearchParams({
        sort_by: 'name',
        sort_dir: 'asc',
        include_inactive: '1',
      });
      qs.set('filter[teacher_id]', String(teacherId));
      return fetchAllPages<Group>('/api/admin/groups', qs);
    },
    enabled: Number.isFinite(teacherId) && teacherId > 0,
    staleTime: 60_000,
  });
}
