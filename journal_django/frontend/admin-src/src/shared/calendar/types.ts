/**
 * Occurrence — data-agnostic форма занятия, которую понимает CalendarView.
 * Копия подмножества teacher-src/src/lib/types.ts (форма GET /api/calendar,
 * teacher_spa/views.py), вынесенная сюда, чтобы shared/calendar не тянул
 * зависимость на teacher-src (shared лежит в admin-src и импортируется ОБОИМИ
 * бандлами — admin через '@', teacher через alias '@shared').
 *
 * teacher-src продолжает пользоваться СВОИМ lib/types.ts (Occurrence там
 * структурно идентичен — TS duck-typing принимает его как проп CalendarView
 * без доп. преобразований). admin-src мапит строки /plan в эту же форму
 * (см. hooks/useGroupPlanCalendar.ts).
 */

export type OccStatus = 'pending' | 'overdue' | 'done' | 'cancelled' | 'moved';

export interface OccStudent {
  name: string;
}

export interface Occurrence {
  /**
   * PlannedLesson.id. Admin-мапинг (useGroupPlanCalendar) заполняет его для
   * операций плана (reschedule/cancel по id); teacher /api/calendar — с тех пор,
   * как LessonForm называет им отмечаемое занятие (submitLesson.plannedLessonId,
   * см. apps/scheduling/services.py::_planned_occurrence_dict). Остаётся optional:
   * у карточек доп.урока позиции курса нет (там extraLessonId).
   */
  id?: number | null;
  /**
   * groups.id занятия — присутствует в ответе build_calendar() (и
   * teacher /api/calendar, и admin /api/admin/calendar — см.
   * services.py _planned_occurrence_dict), но задействовано только
   * кнопкой «Открыть группу» в LessonPopup (onOpenGroup), которую
   * передаёт лишь admin-раздел «Календарь». /api/admin/groups/<id>/plan-
   * мапинг (useGroupPlanCalendar) его не выставляет — там уже открыта
   * карточка группы, ссылка не нужна.
   */
  groupId?: number | null;
  group: string;
  groupDisplay: string;
  teacher: string;
  teacherOverride: string | null;
  direction: string | null;
  color: string | null; // hex #RRGGBB или null
  isGroup: boolean;
  /**
   * Длительность занятия в минутах (groups.lesson_duration_minutes). Teacher
   * /api/calendar отдаёт всегда; admin-мапинг /plan может не заполнять —
   * сетка использует фолбэк 60.
   */
  durationMinutes?: number | null;
  /** Ссылка на чат группы (groups.vk_chat) — контекстное меню teacher-календаря. */
  vkChat?: string | null;
  date: string;         // 'YYYY-MM-DD' — реальная дата занятия
  time: string | null;  // 'HH:MM'
  day: number;           // 0=Вс…6=Сб
  seq: number | null;
  lessonNumber: number | null;
  isHalf: boolean;
  isExtra: boolean;
  /**
   * Присутствует только для карточек ExtraLessonAssignment (доп.урок за
   * пропуск конкретного основного урока, apps.extra_lessons) — отличать от
   * isExtra (групповое доп.занятие вне курса, apps.scheduling.PlannedLesson).
   * CalendarView красит такие карточки фиксированным красным (не по
   * направлению); OccurrenceMenu подставляет «Провести доп.урок».
   */
  extraLessonId?: number | null;
  status: OccStatus;
  label: string;         // готовая подпись (напр. «Перенесён на 10.06»)
  movedFrom: string | null; // 'YYYY-MM-DD'
  movedTo: string | null;   // 'YYYY-MM-DD'
  students: OccStudent[];
}

/** Причины отсутствия плана (зеркало apps/scheduling/repository.py::groups_without_plan). */
export type UnscheduledReason =
  | 'no_start_date'
  | 'no_total_lessons'
  | 'no_slots'
  | 'not_generated';

export interface UnscheduledGroup {
  group: string;
  reason: UnscheduledReason;
}

export interface CalendarResponse {
  occurrences: Occurrence[];
  unscheduled: UnscheduledGroup[];
  window: { from: string; to: string };
}
