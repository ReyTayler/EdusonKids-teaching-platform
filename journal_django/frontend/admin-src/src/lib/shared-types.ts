// Типы 8 сущностей админки. Маппятся 1-в-1 на PG-схему.
// Поля, помеченные `?`, могут отсутствовать в ответе (joined-only).

export type ID = number;

// ===== Teachers =====

/** Привязанный Telegram-аккаунт преподавателя (TelegramRecipient + TelegramUser). */
export interface TeacherTelegram {
  chat_id: number;
  username: string | null;
  full_name: string;
  is_active: boolean;
  /** Почему бот больше не может писать: «заблокировал бота» и т.п. */
  blocked_reason: string | null;
}

export interface Teacher {
  id: ID;
  name: string;
  email: string | null;
  phone: string | null;
  /** Постоянная ссылка на звонок (Телемост, ВК Звонок…); null — не указана. */
  call_url: string | null;
  active: boolean;
  created_at: string;
  /** null — аккаунт не привязан. */
  telegram?: TeacherTelegram | null;
  // joined-only поля (когда запрашиваются с дополнительной информацией):
  groups_count?: number;
}

// ===== Directions =====
export interface Direction {
  id: ID;
  name: string;
  active: boolean;
  total_lessons: number | null;
  color: string | null; // #RRGGBB
  subscription_price: number | string | null; // numeric от pg может прийти строкой
}

// ===== Groups =====
export type LessonDuration = 45 | 60 | 90;

export interface GroupScheduleSlot {
  id?: ID;
  group_id?: ID;
  day_of_week: number; // 0..6
  start_time: string;  // 'HH:MM' or 'HH:MM:SS'
}

export interface Group {
  id: ID;
  name: string;
  direction_id: ID;
  teacher_id: ID;
  is_individual: boolean;
  lesson_duration_minutes: LessonDuration;
  lessons_per_week: number;
  group_start_date: string | null;
  vk_chat: string | null;
  created_at: string;
  active: boolean;
  // Сдвиг нумерации курса: группа-продолжение (перевод в пустую персональную
  // группу) начинает курс с lesson_number_offset+step, а не с 1. 0/undefined —
  // обычная группа. См. LessonGrid (не рисует ячейки 1..offset).
  lesson_number_offset?: number;
  // Ручная длина курса группы в уроках; null/undefined — «как в направлении».
  // Единица — уроки, не занятия: у 45-мин группы 2 урока = 4 занятия.
  lessons_total?: number | null;
  // joined-only:
  direction_name?: string;
  direction_color?: string | null;
  teacher_name?: string;
  slots?: GroupScheduleSlot[];
  members_count?: number;
  // Пройдено УРОКОВ курса (факты `lessons` курсовых типов, 45 мин = 0.5 урока).
  // Та же единица, что lessons_total; numeric(6,1) от pg приходит строкой.
  // Приходит только из списка групп (GET /api/admin/groups).
  lessons_done?: string | number;
}

// ===== Group schedule (GET .../groups/:id/schedule, POST /schedule-change) =====
// Слоты — recurrence-шаблон (group_schedule_slots). Операции над занятиями идут
// через planned_lessons (см. useGroupPlan). lesson_schedule_exceptions удалены (шаг 9).

// Слот с полной историей действия — отличается от GroupScheduleSlot (used в Group.slots / форме
// создания группы) наличием периода действия. id/effective_from здесь всегда заданы сервером.
export interface ScheduleSlot {
  id: ID;
  day_of_week: number;    // 0..6, Вс=0 (см. lib/slots DOW)
  start_time: string;     // 'HH:MM' or 'HH:MM:SS'
  effective_from: string; // 'YYYY-MM-DD'
  effective_to: string | null; // null = слот действует по сей день
}

export interface GroupScheduleData {
  slots: ScheduleSlot[];
}

// ===== Students =====

/** Стадия воронки продлений — «статус» ученика после удаления enrollment_status. */
export interface StudentStage {
  id: ID;
  key: string;
  label: string;
  kind: 'progress' | 'decision' | 'won' | 'lost';
  sort_order: number;
}

export interface Student {
  id: ID;
  full_name: string;
  birth_date: string | null;
  platform_id: string | null;
  /** Ссылка на карточку в CRM (у Eduson — amoCRM); на запись нужен полный URL. */
  crm_link: string | null;
  /** Контакты самого ребёнка. */
  phone: string | null;
  telegram: string | null;
  email: string | null;
  parent1_name: string | null;
  parent1_phone: string | null;
  parent1_email: string | null;
  parent1_telegram: string | null;
  parent2_name: string | null;
  parent2_phone: string | null;
  parent2_email: string | null;
  manager_id: ID | null;
  manager_name: string | null;
  /** Стадия ПОСЛЕДНЕЙ сделки продления; null — сделок ещё не было. */
  stage: StudentStage | null;
  /** false — последняя сделка закрыта (won/lost): бейдж рисуется приглушённым. */
  stage_is_open: boolean;
  /** «До какого месяца заморозка» ('YYYY-MM-01'), непусто только на стадии frozen. */
  stage_frozen_until_month: string | null;
  created_at: string;
}

// ===== Group memberships =====
export interface GroupMembership {
  id: ID;
  group_id: ID;
  student_id: ID;
  lessons_done: string | number; // numeric(6,1) от pg как string
  remaining: string | number;
  start_date: string | null;
  sheet_row: number | null;
  active: boolean;
  // joined-only:
  group_name?: string;
  student_name?: string;
  transferred_from_id?: ID | null;
  transferred_from_group_name?: string | null;
  transferred_from_lessons_done?: string | number | null;
}

// ===== Lessons =====
export type LessonType = 'regular' | 'substitution' | 'reschedule' | 'extra';

export interface Lesson {
  id: ID;
  group_id: ID;
  teacher_id: ID;
  original_teacher_id: ID | null;
  lesson_date: string;     // 'YYYY-MM-DD'
  lesson_number: number;   // numeric(5,1)
  lesson_duration_minutes: LessonDuration;
  lesson_type: LessonType;
  record_url: string | null;
  submitted_at: string;
  submitted_by_token: string;
  // joined-only:
  group_name?: string;
  teacher_name?: string;
  original_teacher_name?: string | null;
}

export interface LessonAttendance {
  lesson_id: ID;
  student_id: ID;
  present: boolean;
  // Исход «бесплатное занятие» (present=true, денег ноль).
  is_free?: boolean;
  // Исход «неоплачиваемый пропуск» (present=false, ученик этот урок не посещает).
  unpaid_skip?: boolean;
  student_name?: string;
  // true — пропуск компенсирован доп.уроком/сожжён: ячейку нельзя флипать в
  // present (двойной учёт), фронт делает её серой/неактивной.
  compensated?: boolean;
}

export interface LessonFull extends Lesson {
  attendance: LessonAttendance[];
  payroll: LessonPayroll | null;
  // Ученики, чей пропуск на этом уроке закрыт доп.уроком/сгоранием. Отдельный
  // список, а не только флаг на строке посещаемости: у ученика, добавленного в
  // группу ПОСЛЕ проведения урока, строки нет вовсе, а компенсация есть — и
  // редактор урока рисует карточки по составу группы, не по строкам.
  compensated_student_ids?: ID[];
}

// ===== Extra lessons (доп. уроки / резолюции пропусков) =====

// Пер-ученик (1:1) резолюция пропуска — одна строка на (пропущенный урок ×
// ученик), заменила групповую ExtraLessonAssignment+participants (Фаза 1a).
export interface AbsenceResolution {
  id: ID;
  student_id: ID;
  student_name: string;
  assigned_teacher_id: ID | null;
  teacher_name: string | null;
  missed_lesson_id: ID | null;            // null для kind='extra' (доп.урок сверх курса)
  missed_lesson_group_id: ID | null;
  missed_lesson_group_name: string | null;
  missed_lesson_date: string | null;
  missed_lesson_number: string | number | null;  // numeric(5,1) — рендерится строкой
  scheduled_date: string;
  scheduled_time: string;
  duration_minutes: number;
  status: 'pending' | 'makeup_scheduled' | 'makeup_done' | 'burned' | 'waived';
  fact_lesson_id: ID | null;
  // kind='makeup' — отработка/сгорание по реальному пропуску; 'extra' — доп.урок
  // СВЕРХ курса, назначенный вручную (missed_lesson нет, группа в group_id).
  kind: 'makeup' | 'extra';
  group_id: ID | null;                    // группа extra-резолюции (для makeup null)
  target_lesson_number: string | number | null;  // «за какой урок» для extra
  resolution_group_name: string | null;   // унифицированное имя группы (makeup|extra)
}

// Ответ POST /api/admin/extra-lessons — multi-select создаёт N резолюций.
export interface ExtraLessonCreateResult {
  created: number;
  resolution_ids: number[];
}

// ===== Payroll =====
// Зарплату считает сервер (движок мотивации). Деньги приходят строками —
// точный Decimal, арифметику над ними на фронте не делаем.
export type PayrollKind = 'lesson' | 'cancellation';

/** Строка общего списка «Зарплата»: урок или оплаченная отмена. */
export interface PayrollEntry {
  kind: PayrollKind;
  /** Уникален только вместе с kind: у уроков и отмен разные таблицы. */
  id: ID;
  lesson_id: ID | null;
  teacher_id: ID;
  total_students: number | null;
  present_count: number | null;
  payment: string;
  penalty: string;
  lesson_date: string;
  scheduled_time: string | null;
  lesson_number: string | null;
  group_id: ID;
  group_name: string;
  teacher_name: string;
  is_manual: boolean;
  calc_payment: string;
  calc_penalty: string | null;
  rule_note: string;
}

/** Начисление за урок в карточке урока (GET /api/admin/lessons/<id>). */
export interface LessonPayroll {
  id: ID;
  total_students: number;
  present_count: number;
  payment: string;
  penalty: string;
  rule_note: string;
  is_manual: boolean;
  calc_payment: string;
  calc_penalty: string | null;
}

export interface PayrollSummaryRow {
  teacher_id: ID;
  teacher_name: string;
  /** Счётчики приходят строками (bigint → text). */
  lessons_count: string;
  cancellations_count: string;
  sum_lessons_payment: string;
  sum_cancellations_payment: string;
  /** Уроки + отмены. */
  sum_payment: string;
  sum_penalty: string;
}

// ===== Archive (sidebar shape) =====
export interface ArchivePayload {
  teachers: Teacher[];
  groups: Group[];
  directions: Direction[];
}

// ===== Common =====
export interface ApiErrorBody {
  error: string;
  details?: unknown;
}

// ===== Payments =====

export interface Payment {
  id: ID;
  student_id: ID;
  direction_id: ID | null;
  subscriptions_count: number | null;
  unit_price: number | string;   // numeric(10,2) → строка от pg
  total_amount: number | string;
  lessons_count: number | null;
  kind: 'purchase' | 'refund' | 'surcharge';
  paid_at: string;               // 'YYYY-MM-DD'
  note: string | null;
  created_at: string;
  created_by: string | null;
  // Доплата к абонементу (kind='surcharge'): деньги без уроков, поднимающие цену
  // конкретного блока родительской оплаты.
  parent_payment_id?: number | null;
  subscription_index?: number | null;
  // joined-only:
  student_name?: string;
  direction_name?: string;
}

// ===== Balance =====

export interface PaidByDirection {
  direction_id: ID;
  direction_name: string;
  direction_color: string | null;
  total_paid_amount: number | string;
}

export interface AttendedByDirection {
  direction_id: ID;
  direction_name: string;
  direction_color: string | null;
  attended_lessons: number;
}

export interface StudentBalance {
  total_balance: number;              // общий пул ученика, не по направлению
  total_paid_amount: number | string;
  remaining_value: number;            // неотработанный остаток в деньгах
  paid_by_direction: PaidByDirection[];
  attended_by_direction: AttendedByDirection[];
  payments: Payment[];
}

// ===== Discounts =====
export interface Discount {
  id: ID;
  name: string;
  amount: number | string;  // numeric(5,4) → строка от pg, 0..1
  active: boolean;
  created_at: string;
}

// ===== Pagination =====

export interface Paginated<T> {
  rows: T[];
  total: number;
  page: number;
  page_size: number;
}

// ===== Обратная связь по уроку (GET /api/admin/lesson-feedback) =====

export interface LessonFeedbackRow {
  id: number;
  /** null — урок удалён, а отзыв остался (связь с уроком обнуляется). */
  lesson_id: number | null;
  group_id: number;
  group_name: string;
  lesson_date: string;
  record_url: string | null;
  teacher_id: number | null;
  teacher_name: string | null;
  /** false — отзыв «урок не состоялся» (импорт): урока нет, оценок нет. */
  took_place: boolean | null;
  /** null — оценки нет (урок не состоялся). */
  understanding: number | null;
  interest: number | null;
  tech_problems: boolean;
  comment: string;
  submitted_at: string;
}

export interface UnfilledLesson {
  kind: 'planned' | 'extra';
  id: number;
  group_id: number;
  group_name: string;
  teacher_name: string | null;
  direction_name: string | null;
  direction_color: string | null;
  lesson_number: number | null;
  date: string;        // 'YYYY-MM-DD'
  time: string | null; // 'HH:MM'
}

// ===== Реестр куратора (вкладка дашборда) =====

export type RegistryStatus = 'closed' | 'ending' | 'idle' | 'no_plan' | 'ok';
export type RegistrySegment = 'all' | 'ending' | 'closed' | 'idle' | 'no_plan' | 'cancellations';

export interface RegistryKpis {
  active_students: number;
  renewal_upsell: number;
  idle: number;
  avg_progress: number;
  lessons_ahead: number;
  /** Число ОТМЕНЁННЫХ ЗАНЯТИЙ за месяц (не учеников — у сигнала другая единица). */
  cancellations: number;
}

export interface TodayStreamItem {
  time: string | null;
  group_id: number;
  group_code: string;
  teacher_name: string | null;
  student_names: string[];
  status: string;
  /** Длительность занятия группы (45/60/90) — из неё фронт считает время конца. */
  duration_minutes: number;
}

export interface RegistrySignal {
  count: number;
}

export interface RegistrySummary {
  generated_at: string;
  kpis: RegistryKpis;
  today_stream: TodayStreamItem[];
  /** count — число УЧЕНИКОВ, попавших в сигнал. */
  signals: Record<'ending' | 'closed' | 'idle' | 'no_plan' | 'cancellations', RegistrySignal>;
}

export interface RegistryStudent {
  student_id: number;
  student_name: string;
  codes: string[];
  teacher_names: string[];
  /** Остаток уроков курса (может быть 0.5 и отрицательным); null — нет активного курса. */
  balance: number | null;
  attended: number;
  planned: number;
  progress_pct: number | null;
  last_lesson_date: string | null;
  next_lesson_date: string | null;
  status: RegistryStatus;
  /** Отмены в текущем месяце в группах ученика. */
  cancellations: number;
}

// ===== Accounts & RBAC =====

export type Role = 'teacher' | 'manager' | 'admin' | 'superadmin';

export type AccountStatus = 'invited' | 'active' | 'expired' | 'disabled';

export interface Account {
  id: number;
  email: string;
  role: Role;
  teacher_id: number | null;
  teacher_name?: string | null;
  full_name?: string | null;
  // Вычисляемое сервером имя: full_name || teacher_name || email.
  name?: string;
  active: boolean;
  twofa_enabled: boolean;
  twofa_method: 'totp' | 'email' | null;
  last_login_at: string | null;
  // Вычисляемые сервером (список): есть ли активный invite и сводный статус.
  has_active_invite?: boolean;
  status?: AccountStatus;
}

export interface AuditEntry {
  id: number | string; // bigserial → pg отдаёт строкой
  occurred_at: string;
  account_id: number | null;
  account_email?: string | null;
  actor_email: string | null;
  event: string;
  ip: string | null;
  target_id: number | null;
  meta: unknown;
}

// ===== Dashboard =====

export interface DashboardData {
  month: string;            // 'YYYY-MM' — текущий МСК-месяц
  from: string | null;      // применённый период 'YYYY-MM-DD' (дефолт — последние 3 месяца)
  to: string | null;        // null — открытый конец при заданном только from
  revenue_month: number;    // собрано за период
  worked_off_month: number; // отработано за месяц (FIFO)
  carryover_month: number;  // revenue_month − worked_off_month (может быть < 0)
  deferred_total: number;   // снимок несписанных партий, ≥ 0
  // Признанная выручка (FIFO) по дням и месяцам периода — график «Recognized revenue».
  // Сумма ряда равна worked_off_month (считаются одним проходом).
  recognized_daily: { date: string; recognized: number }[];
  recognized_monthly: { month: string; recognized: number }[];
}

/** GET /api/admin/dashboard/revenue — поступления за период (обе границы включительно). */
export interface RevenuePoint {
  revenue: number;          // поступления за день/месяц, без возвратов
  orders: number;           // число оплат с суммой > 0
  aov: number | null;       // средний чек = revenue / orders; null, если оплат нет
}

/** Строка сводной таблицы по курсам (дашборд «Финансы»). */
export interface ProductRow {
  direction_id: number | null;   // null — легаси-оплаты без направления
  direction: string | null;      // имя курса; null у той же легаси-строки
  purchases: number;             // покупок курса за период
  months: number;                // оплачено месяцев (поштучный урок = 0.25)
  revenue: number;               // все поступления курса, без возвратов
  asp: number | null;            // revenue / purchases; нет покупок → null
  arpm: number | null;           // среднее по заказам «цена ÷ срок»; нет срока → null
}

export interface RevenueData {
  from: string;             // применённый период 'YYYY-MM-DD'
  to: string;
  revenue: number;
  orders: number;
  aov: number | null;
  daily: (RevenuePoint & { date: string })[];    // каждый день периода, пустые — нули
  monthly: (RevenuePoint & { month: string })[]; // 'YYYY-MM', месяцы периода
  products: ProductRow[];                        // разрез той же выручки по курсам
}

// ===== Changelog (журнал изменений данных) =====
// Контракт: apps/changelog (repository.list_operations / get_operation / revert).

export interface ChangelogActor {
  account_id: number;
  email: string | null;
  name: string | null;   // имя преподавателя / ФИО / fallback email
  role: Role | null;
}

/** Сводка по одной сущности внутри операции (лента). */
export interface ChangelogEntitySummary {
  entity: string;   // ключ из apps/changelog/registry.py → CHANGELOG_ENTITY_LABELS
  inserts: number;
  updates: number;
  deletes: number;
}

/** Строка ленты: 1 строка = 1 операция (pghistory-контекст). */
export interface ChangelogOperation {
  id: string;                 // uuid контекста
  occurred_at: string;
  actor: ChangelogActor | null; // null = вне HTTP (management-команда)
  operation: string;          // ключ → CHANGELOG_OPERATION_LABELS
  summary: string;            // человекочитаемое описание (бэкенд)
  url: string | null;
  method: string | null;
  entities: ChangelogEntitySummary[];
  events_total: number;
  revertable: boolean;
  reverted: boolean;          // операция уже откатывалась
}

export type ChangelogEventLabel = 'insert' | 'update' | 'delete';

/** Одно очеловеченное изменение поля внутри события. */
export interface ChangelogFieldChange {
  label: string;          // русская подпись поля
  old: string | null;     // null у создания
  new: string | null;     // null у удаления
}

/** Полностью очеловеченное представление события (бэкенд). */
export interface ChangelogEventHuman {
  title: string;                       // «Ученик Иван Петров»
  text: string;                        // готовая фраза (= description)
  changes: ChangelogFieldChange[];
}

/** Одно row-событие внутри операции (детальная карточка). */
export interface ChangelogEvent {
  model: string;              // 'groups.Group'
  entity: string;
  obj_id: number | string | null;
  label: ChangelogEventLabel;
  data: Record<string, unknown>;
  /** поле → [было, стало]; null у insert/первого события строки. */
  diff: Record<string, [unknown, unknown]> | null;
  /** Готовая человекочитаемая фраза (бэкенд). Опционально — на случай старого кэша. */
  description?: string;
  /** Полностью очеловеченное представление (бэкенд). Опционально — на случай старого кэша. */
  human?: ChangelogEventHuman;
}

export interface ChangelogDetail {
  id: string;
  occurred_at: string;
  actor: ChangelogActor | null;
  operation: string;
  summary: string;
  url: string | null;
  method: string | null;
  revertable: boolean;
  reverted: boolean;
  events: ChangelogEvent[];
  /** Готовая русская причина недоступности отката; null = откат доступен. Опционально — старый кэш. */
  not_revertable_reason?: string | null;
}

export interface RevertResult {
  reverted_events: number;
  inserts_undone: number;
  deletes_undone: number;
  updates_undone: number;
}

/** Элемент details.conflicts в 409-ответе POST .../revert. */
export interface RevertConflictItem {
  model: string;   // 'groups.Group'
  entity: string;  // ключ → CHANGELOG_ENTITY_LABELS
  obj_id: number | string | null;
  reason: 'row_exists' | 'row_missing' | 'changed_later' | 'no_previous_state';
  fields?: string[];
}

// ===== Вкладка «Уроки» карточки ученика (спека 2026-09-14) =====

/** Выводимый тип строки. Считает бэкенд из lesson_type + is_free
 *  (apps/students/lesson_history.py) — это НЕ сырой lessons.lesson_type. */
export type StudentLessonKind = 'regular' | 'free' | 'extra' | 'burned';

export interface StudentLessonRow {
  lesson_id: number;
  /** Номер урока в плане курса, numeric(5,1) строкой («12.0»). У доп.урока и
   *  сгорания унаследован от пропущенного занятия. Рендерить через fmtLessons. */
  lesson_number: string;
  lesson_date: string;
  submitted_at: string;
  kind: StudentLessonKind;
  duration_minutes: number;
  group_id: number;
  group_name: string;
  teacher_id: number;
  teacher_name: string;
  direction_id: number;
  direction_name: string;
  /** Признанные деньги строкой — точный Decimal с бэка, fmtRub принимает строку. */
  recognized_amount: string;
  /** Урок (целиком или частично) прошёл сверх оплаченного остатка. */
  is_debt: boolean;
}

// ===== Дашборд «Посещения учеников» (спека 2026-09-15) =====

/** Две цифры экрана. Уроки с весом (45 мин = 0,5) строками — точный Decimal. */
export interface AttendanceDashboardSummary {
  all_time_lessons: string;
  period_lessons: string;
}

/** Строка ученика: разбивка занятий внутри выбранного периода, всё в уроках. */
export interface AttendanceDashboardRow {
  student_id: number;
  full_name: string;
  regular: string;
  extra: string;
  burned: string;
  free: string;
  /** Итого списано = обычные + доп.уроки + сгоревшие. Бесплатные не входят. */
  billed: string;
}

// ===== Мотивация: ставки школы и персональные ставки (Настройки → Мотивация) =====
// Деньги и проценты приходят строками ('600.00') — точный Decimal, арифметики над ними нет.

export type CancelMode = 'percent' | 'fixed';
export type PenaltyMode = 'per_lesson' | 'per_student';
export type PersonalRateKind = 'fixed' | 'plus_fixed' | 'plus_percent';

export interface RateVersionDirection {
  direction_id: ID;
  direction_name: string;
  /** null — для направления не задано, действует ставка по умолчанию. */
  group_rate: string | null;
  individual_rate: string | null;
}

export interface RateVersion {
  id: ID;
  effective_from: string;
  is_current: boolean;
  group_rate: string;
  individual_rate: string;
  cancel_mode: CancelMode;
  cancel_value: string;
  penalty_enabled: boolean;
  penalty_mode: PenaltyMode;
  penalty_amount: string;
  created_by: ID | null;
  created_by_name: string | null;
  created_at: string;
  directions: RateVersionDirection[];
  /** Править и удалять можно только версии, которые ещё не начали действовать. */
  editable: boolean;
}

export interface RateVersionsResponse {
  results: RateVersion[];
  /** Уроки, для которых нет ни одной версии ставок (раньше самой ранней). */
  no_rate: { lessons: number; earliest: string | null };
  directions: { id: ID; name: string; color: string | null }[];
}

export interface RateVersionPayload {
  effective_from: string;
  group_rate: string;
  individual_rate: string;
  cancel_mode: CancelMode;
  cancel_value: string;
  penalty_enabled: boolean;
  penalty_mode: PenaltyMode;
  penalty_amount: string;
  directions: { direction_id: ID; group_rate: string | null; individual_rate: string | null }[];
}

/** Ответ мутаций: сколько начислений пересчитал сервер. */
export interface RecalculatedInfo { recalculated: number }

export interface PersonalRate {
  id: ID;
  teacher_id: ID;
  teacher_name: string;
  group_id: ID;
  group_name: string;
  direction_id: ID | null;
  direction_name: string | null;
  direction_color: string | null;
  /** Номера уроков — строки ('10.0'); null/null — ставка на все уроки группы. */
  lesson_from: string | null;
  lesson_to: string | null;
  kind: PersonalRateKind;
  value: string;
  note: string;
  created_by_name: string | null;
  created_at: string;
  /** Группу сейчас ведёт другой преподаватель (ставка осталась от прежнего). */
  group_teacher_id: ID | null;
  group_of_other_teacher: boolean;
  /** Выключенная ставка не действует с сегодняшнего дня. */
  is_active: boolean;
  term_id: ID;
  /** Начало текущего (у выключенной — последнего) периода; null — с начала. */
  valid_from: string | null;
  /** Конец периода (не включительно); null — бессрочно. */
  valid_until: string | null;
  /** История периодов, от старых к новым. */
  terms: PersonalRateTerm[];
}

export interface PersonalRateTerm {
  id: ID;
  valid_from: string | null;
  valid_until: string | null;
  lesson_from: string | null;
  lesson_to: string | null;
  kind: PersonalRateKind;
  value: string;
}

/** Правка ставки: преподаватель и группа не меняются, шлются только параметры и примечание. */
export type PersonalRateUpdatePayload =
  Partial<Pick<PersonalRatePayload, 'lesson_from' | 'lesson_to' | 'kind' | 'value' | 'note'>>;

export interface PersonalRatePayload {
  teacher_id: ID;
  group_id: ID;
  lesson_from?: string | null;
  lesson_to?: string | null;
  kind: PersonalRateKind;
  value: string;
  note?: string;
}

// ===== Причины отмены уроков =====

export type CancellationSide = 'teacher' | 'client';

export interface CancellationReason {
  id: ID;
  side: CancellationSide;
  name: string;
  position: number;
  /** Только для superadmin: решение о деньгах преподавателя конфиденциально. */
  compensation?: boolean;
}

/** GET /api/admin/cancellation-reasons — активные причины по сторонам. */
export type CancellationReasonsResponse = Record<CancellationSide, CancellationReason[]>;

export interface CancellationReasonCreatePayload {
  side: CancellationSide;
  name: string;
  compensation?: boolean;
}

/** Сторона причины неизменяема, поэтому в PATCH её нет. */
export interface CancellationReasonUpdatePayload {
  name?: string;
  compensation?: boolean;
  position?: number;
}

/** Тело POST /plan/<lid>/cancel: причина XOR свободный текст; notified_on — только для client. */
export interface CancelLessonPayload {
  lesson_date: string;
  side: CancellationSide;
  reason_id?: ID | null;
  other_text?: string | null;
  notified_on?: string | null;
}

/** Строка вкладки группы «Отмены уроков». */
export interface LessonCancellationRow {
  id: ID;
  lesson_date: string;
  scheduled_time: string; // 'HH:MM'
  side: CancellationSide;
  reason_text: string;
  is_other: boolean;
  notified_on: string | null;
  teacher_id: ID | null;
  teacher_name: string | null;
  created_at: string;
  created_by_name: string | null;
}
