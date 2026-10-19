/**
 * Базовая форма занятия. Осталась от /api/report (эндпоинт удалён 2026-09-30) — на ней
 * держатся статусы и определение предмета по группе (lib/subjects.ts).
 */

export type LessonStatus = 'done' | 'pending' | 'overdue' | 'notime';

export interface ReportStudent {
  name: string;
}

export interface ReportLesson {
  teacher: string;
  group: string;
  pm: string;
  vkChat: string;
  startDate: string;
  isGroup: boolean | null;
  students: ReportStudent[];
  groupDisplay: string;
  day: number | null;       // 1=Пн … 6=Сб, 0=Вс
  dayName: string | null;
  dayShort: string | null;
  time: string | null;      // 'HH:MM'
  sortKey?: number;
  status: LessonStatus;
  label: string;
}

/** Формы ответов /api/getData, /api/getAllData, /api/submitLesson (заморожены). */

export interface TStudent {
  /** Первичный ключ ученика: имена не уникальны (тёзки), различаем по id. */
  id: number;
  name: string;
  lessonsDone: number;
  remaining: number;
  // Дата рождения 'YYYY-MM-DD' | '' (поле age удалено; возраст teacher-фронт
  // при необходимости считает сам). Сейчас нигде не отображается.
  birthDate: string;
  sheetName: string;
  sheetRow: number;
  // Накопленный прогресс в прежней группе у переведённого ученика (null — не переводился).
  // Ученика нельзя отмечать, пока номер урока <= lockedThrough: группа ещё не догнала
  // его прогресс. Готового флага с сервера нет намеренно — номер заполняемого урока
  // знает только форма записи (он из плана занятия, а не из прогресса группы).
  lockedThrough: number | null;
}

export interface GroupData {
  students: TStudent[];
  lessonsDone: number;
  pm: string;
  vkChat: string;
  startDate: string;
  isGroup: boolean;
  // Гард оплат школы (settings.PAYMENT_BALANCE_GUARD на сервере). true — ученика с
  // remaining <= 0 отметить нельзя («Нет оплаты»), у группы виден «Долг». false —
  // оплаты в платформе не ведутся (Eduson): remaining смысла не несёт, не блокирует
  // и не показывается. Флаг школы, а не группы; едет в каждой группе, потому что все
  // потребители получают именно GroupData. Сервер проверяет тот же флаг сам.
  paymentGuard: boolean;
  // «Неоплачиваемый пропуск» (пометки LessonSkip менеджера: перевод / начал не с 1-го
  // урока) по номерам уроков: { '3': ['Петя Иванов'], '3.5': [...] }. Ключ — номер
  // урока как его даёт String(number); half-lesson даёт дробный. Отмечать таких
  // учеников нельзя, в зарплату они не входят. Карта, а не флаг у ученика: применять
  // её нужно к номеру ЗАПОЛНЯЕМОГО урока, а он известен только форме записи.
  skips?: Record<string, string[]>;
  // То же по id учеников: { '3': [12, 40] }. Имена не уникальны, поэтому форма
  // записи опирается на это поле; skips оставлен только как запасной путь для
  // старого ответа сервера без skipIds.
  skipIds?: Record<string, number[]>;
}

/** Ключи — названия групп. */
export type GroupMap = Record<string, GroupData>;

/** POST /api/getData — группы ТОЛЬКО текущего преподавателя, без вложенности. */
export interface GetDataResponse {
  teacher: string;
  data: GroupMap;
}

/** POST /api/getAllData — свои группы + чужие с назначенным занятием, вложено по преподавателю. */
export interface GetAllDataResponse {
  teacher: string;
  data: Record<string, GroupMap>;
}

/**
 * isSubstitution/originalTeacher/lessonType удалены: тип урока выводит сервер
 * из planned_lessons — замену из назначения «Сменить преподавателя» (admin),
 * перенос из moved_from_date плановой строки; присланные клиентом поля
 * отклоняются с 400.
 */
export interface SubmitPayload {
  group: string;
  date: string; // 'YYYY-MM-DD'
  recordUrl?: string;
  // id обязателен: без него урок группы с тёзками сервер не сохранит. name оставлен для журнала/старых клиентов.
  students: { id: number; name: string; present: boolean }[];
  /** Позиция курса (Occurrence.id) — какое именно занятие отмечается. Шлют оба
   *  входа, и календарь, и «Мои уроки»: без него сервер угадывает позицию по
   *  дате и на группах с двумя занятиями в день уходит на путь, где повторная
   *  отправка создаёт второй платный урок. Отсутствует только там, где позиции
   *  нет вовсе (группа без плана занятий). */
  plannedLessonId?: number;
  /** Обратная связь по уроку — обязательна (сервер вернёт 400 без неё). */
  feedback: LessonFeedbackPayload;
}

export interface LessonFeedbackPayload {
  understanding: number; // 1–10
  interest: number;      // 1–10
  techProblems: boolean; // без значения по умолчанию: преподаватель отвечает явно
  comment: string;       // непустой, ≤5000
}

export type SubmitResult =
  | { success: true; payment: number; penalty: number; lessonNumber: number }
  // code — машинный код отказа: 'roster_stale' (состав группы изменился), 'ambiguous_name'.
  | { success: false; error: string; code?: 'roster_stale' | 'ambiguous_name' };

/** Статус ученика в истории «Мои уроки» — выводит сервер
 *  (teacher_spa/serializers.py::my_lesson_student_status). */
export type MyLessonStudentStatus = 'present' | 'free' | 'absent' | 'skip' | 'burned';

export interface MyLessonStudent {
  id: number;
  name: string;
  status: MyLessonStudentStatus;
}

/**
 * Форма элемента ответа GET /api/lessons (заморожена; см.
 * teacher_spa/serializers.py::MyLessonSerializer). ВНИМАНИЕ: lessonNumber/
 * payment/penalty приходят строками (Decimal, DateSafeJSONRenderer) — на
 * фронте приводить через Number(), с guard на null.
 */
export interface MyLesson {
  id: number;
  date: string; // 'YYYY-MM-DD'
  group: string;
  lessonNumber: string;
  /** Group.lesson_number_offset: уроки 1..N пройдены до перехода на платформу. */
  lessonNumberOffset?: string; // Decimal строкой, как lessonNumber
  lessonType: 'regular' | 'substitution' | 'reschedule' | 'extra' | 'burned';
  isSubstitution: boolean;
  originalTeacher: string | null;
  recordUrl: string | null;
  submittedAt: string; // ISO
  presentCount: number | null;
  totalCount: number | null;
  payment: string | null;
  penalty: string | null;
  direction: string | null;
  directionColor: string | null;
  /** Ученики урока по алфавиту с пометкой посещения. */
  students: MyLessonStudent[];
}

/** GET /api/lessons — StandardPagination envelope. */
export interface MyLessonsResponse {
  rows: MyLesson[];
  total: number;
  page: number;
  page_size: number;
}

/** Элемент карты GET /api/group-directions — направление конкретной группы. */
export interface GroupDirection {
  direction: string | null;
  color: string | null;
  isIndividual: boolean;
  /** Длительность урока в минутах (напр. 45/60/90) — первичный источник half-lesson (Ф4). */
  lessonDurationMinutes: number;
  /** Лимит уроков по курсу направления; null — лимита нет/неизвестен (Ф4). */
  totalLessons: number | null;
}

/** GET /api/group-directions — карта ВСЕХ активных групп преподавателя: имя → направление. */
export interface GroupDirectionsResponse {
  groups: Record<string, GroupDirection>;
}

/**
 * Форма ответа GET /api/calendar (заморожена; см. teacher_spa/views.py).
 * В отличие от /api/report, occurrence привязан к РЕАЛЬНОЙ дате занятия
 * (occ.date), а не к дню недели+номеру недели — окно произвольное (≤92 дней),
 * поэтому месяц можно запросить ОДНИМ запросом. direction/color приходят
 * прямо в occurrence — отдельного useGroupDirections для календаря не нужно.
 */
export type OccStatus = 'pending' | 'overdue' | 'done' | 'cancelled' | 'moved';

export interface OccStudent {
  name: string;
}

export interface Occurrence {
  /**
   * PlannedLesson.id — позиция курса этого занятия. Ею LessonForm называет
   * серверу, КАКОЙ урок отмечается (submitLesson.plannedLessonId): сервер берёт
   * из позиции номер урока и закрепляет за ней факт, поэтому повторная отправка
   * упирается в занятую позицию (409) вместо создания второго урока.
   * null у карточек доп.урока — они отмечаются своим путём (extraLessonId).
   */
  id: number | null;
  group: string;
  groupDisplay: string;
  teacher: string;
  teacherOverride: string | null;
  direction: string | null;
  color: string | null; // hex #RRGGBB или null
  isGroup: boolean;
  /** Длительность занятия в минутах (groups.lesson_duration_minutes) — высота ячейки в сетке. */
  durationMinutes: number;
  /** Ссылка на чат группы (groups.vk_chat) — пункт «Перейти в чат» контекстного меню. */
  vkChat: string | null;
  date: string;         // 'YYYY-MM-DD' — реальная дата занятия
  time: string | null;  // 'HH:MM'
  day: number;           // 0=Вс…6=Сб (как раньше report.day)
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

export interface UnscheduledGroup {
  group: string;
  reason: 'no_start_date' | 'no_slots';
}

export interface CalendarResponse {
  occurrences: Occurrence[];
  unscheduled: UnscheduledGroup[];
  window: { from: string; to: string };
}

/**
 * GET /api/my-payroll?month=YYYY-MM — своя зарплата за месяц с расшифровкой
 * (apps/payroll/views.py::MyPayrollView). Деньги приходят строками с масштабом
 * ('800.00') — как во всём teacher-контракте; на фронте форматировать через
 * lib/money.ts, а не Number() + toFixed по месту.
 */
export type PayrollRuleCode =
  | 'per_student'
  | 'small_group_full'
  | 'small_group_partial'
  | 'half_lesson'
  | 'extra_flat'
  | 'extra_individual'
  | 'none'
  | 'adjusted';

export interface PayrollRule {
  code: PayrollRuleCode;
  /** Короткая формула для строки урока: '4 × 200 ₽'. */
  text: string;
  /** Правило словами: 'группа от 3 человек — 200 ₽ за каждого пришедшего'. */
  note: string;
}

export type PayrollLessonKind =
  | 'regular' | 'substitution' | 'reschedule' | 'extra' | 'burned';

export interface PayrollEntry {
  lessonId: number;
  date: string;             // 'YYYY-MM-DD'
  group: string;
  direction: string | null;
  directionColor: string | null;
  lessonNumber: string;
  kind: PayrollLessonKind;
  durationMinutes: number;
  totalStudents: number;
  presentCount: number;
  payment: string;          // начислено
  penalty: string;          // удержано
  net: string;              // к выплате за этот урок
  rule: PayrollRule;
  /** Почему удержали; null — штрафа нет. */
  penaltyNote: string | null;
  /** Почему headcount меньше группы (бесплатные/непл. пропуски); null — все учтены. */
  excludedNote: string | null;
  /** Сумму правил администратор — формулой она не объясняется. */
  adjusted: boolean;
}

export interface MyPayrollResponse {
  month: string;            // 'YYYY-MM'
  monthLabel: string;       // 'Июль 2026'
  totals: {
    lessons: number;
    presences: number;
    payment: string;
    penalty: string;
    net: string;
  };
  rows: PayrollEntry[];
}
