import type { CancelMode, CancellationSide, LessonType, PayrollKind, PenaltyMode, PersonalRateKind, RegistrySegment, RegistryStatus, Role, StudentLessonKind } from './types';

// Статуса ученика больше нет (спека 2026-07-25): его «статус» — стадия последней
// сделки продления, подписи стадий приходят с бэка (useRenewalStages).

// ===== «Пройдено до перехода на платформу» =====
// Group.lesson_number_offset = N: первые N уроков группа прошла в старой системе,
// их даты не сохранились, известно только количество. Одна формулировка на все
// экраны (admin и teacher).
const fmtOffset = (n: number): string =>
  Number.isInteger(n) ? String(n) : n.toFixed(1).replace('.', ',');

export function preplatformNoteText(offset: number): string {
  const tail = 'до перехода на платформу — даты в старой системе не сохранились';
  if (offset === 1) return `Урок 1 пройден ${tail}`;
  if (offset < 1) return `Пройдено ${fmtOffset(offset)} урока ${tail}`;
  return `Уроки 1–${fmtOffset(offset)} пройдены ${tail}`;
}

// ===== Роли учётки =====
// AccountsPage.tsx держит собственную локальную копию (не тронута — вне
// зоны этой правки), это единственный переиспользуемый экземпляр для
// нового кода (см. ReaderRolesField в разделе «Wiki»).

export const ROLE_LABELS: Record<Role, string> = {
  teacher:    'Преподаватель',
  manager:    'Менеджер',
  admin:      'Администратор',
  superadmin: 'Суперадминистратор',
};

// ===== Реестр куратора — статус ученика =====
// Подписи бейджа и строк сигналов. Коды приходят с бэка (registry_service.classify).

export const REGISTRY_STATUS_LABELS: Record<RegistryStatus, string> = {
  closed:  'Курс пройден',
  ending:  'Заканчивается',
  idle:    'Простой',
  no_plan: 'Нет плана',
  ok:      'Активен',
};

// Подписи сегментов и строк «Сигналов менеджеру». Единица — ученики.
export const REGISTRY_SEGMENT_LABELS: Record<RegistrySegment, string> = {
  all:           'Все',
  ending:        'Заканчивается',
  closed:        'Курс пройден',
  idle:          'Простой',
  no_plan:       'Нет плана',
  cancellations: 'Есть отмены',
};

export const REGISTRY_SIGNAL_LABELS: Record<Exclude<RegistrySegment, 'all'>, string> = {
  ending:        'Заканчивается (осталось ≤2 урока) — предложить продление',
  closed:        'Курс пройден — предложить новый',
  idle:          'Простой (больше 14 дней без урока)',
  no_plan:       'Нет плана (нет будущих занятий)',
  cancellations: 'Есть отмены занятий в этом месяце',
};

// ===== Lesson type =====

export const LESSON_TYPE_LABELS: Record<LessonType, string> = {
  regular:      'обычный',
  substitution: 'замена',
  reschedule:   'перенос',
  extra:        'доп.урок',
};

export const LESSON_TYPE_OPTIONS: { value: LessonType; label: string }[] =
  (Object.entries(LESSON_TYPE_LABELS) as [LessonType, string][])
    .map(([value, label]) => ({ value, label }));

// ===== Вид строки в «Зарплате» =====
// Оплачиваются не только уроки, но и отменённые занятия — у них свои эндпоинты,
// поэтому вид строки различаем явно.
export const PAYROLL_KIND_LABELS: Record<PayrollKind, string> = {
  lesson:       'Урок',
  cancellation: 'Отмена',
};

export const PAYROLL_KIND_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'lesson',       label: 'Уроки' },
  { value: 'cancellation', label: 'Отмены' },
];

// ===== Тип строки во вкладке «Уроки» карточки ученика =====
// Отдельный набор, а не LESSON_TYPE_LABELS: тот описывает сырой lessons.lesson_type
// и не знает ни «сгоревшего», ни «бесплатного» (последний — вовсе не тип урока,
// а исход посещаемости). Коды выводит бэкенд (apps/students/lesson_history.py).

export const STUDENT_LESSON_KIND_LABELS: Record<StudentLessonKind, string> = {
  regular: 'Обычный',
  free:    'Бесплатный',
  extra:   'Доп.урок',
  burned:  'Сгоревший',
};

// ===== Changelog: операции журнала изменений =====
// Ключи — из apps/changelog/labels.py (бэкенд выводит их из method+url).

/** Разовые операции плана, которые сбрасываются при смене расписания. */
export const AFFECTED_OP_LABELS: Record<'reschedule' | 'substitution' | 'cancellation', string> = {
  reschedule: 'перенос',
  substitution: 'замена преподавателя',
  cancellation: 'отмена',
};

// Короткие названия действий — колонка «Действие» и dropdown «Все действия».
export const CHANGELOG_OPERATION_LABELS:Record<string, string> = {
  'group.create':                  'Новая группа',
  'group.update':                  'Правка группы',
  'group.delete':                  'Группа в архив',
  'group.schedule_change':         'Смена расписания',
  'plan.generate':                 'Генерация плана',
  'plan.permanent_change':         'Смена расписания (план)',
  'plan.change_teacher_permanent': 'Смена преподавателя',
  'plan.change_teacher':           'Замена преподавателя',
  'plan.extra':                    'Доп. занятие',
  'plan.reschedule':               'Перенос урока',
  'plan.cancel':                   'Отмена урока',
  'direction.create':              'Новое направление',
  'direction.update':              'Правка направления',
  'direction.delete':              'Направление в архив',
  'teacher.create':                'Новый преподаватель',
  'teacher.update':                'Правка преподавателя',
  'teacher.delete':                'Преподаватель в архив',
  'student.create':                'Новый ученик',
  'student.update':                'Правка ученика',
  'student.manager_update':        'Смена менеджера ученика',
  'student.delete':                'Ученик в архив',
  'student.status':                'Смена статуса ученика',
  'student.resume':                'Выход из заморозки',
  'discount.create':               'Новая скидка',
  'discount.update':               'Правка скидки',
  'discount.delete':               'Скидка в архив',
  'membership.create':             'Зачисление',
  'membership.place':              'Перевод / запись в группу',
  'membership.update':             'Правка членства',
  'membership.delete':             'Отчисление',
  'payment.create':                'Оплата',
  'payment.delete':                'Удаление оплаты',
  'lesson.submit':                 'Проведение урока',
  'lesson.create':                 'Создание урока',
  'lesson.update':                 'Правка урока',
  'lesson.delete':                 'Удаление урока',
  'lesson.attendance_update':      'Правка посещаемости',
  'extra_lesson.create':           'Назначение доп.урока',
  'extra_lesson.cancel':           'Отмена доп.урока',
  'extra_lesson.delete':           'Удаление доп.урока',
  'extra_lesson.record':           'Проведение доп.урока',
  'extra_lesson.burn':             'Сгорание пропуска',
  // Действие снято, но метка остаётся для показа исторических событий в журнале.
  'extra_lesson.waive':            'Закрытие пропуска без денег',
  'payroll.update':                'Правка начисления',
  'payroll.reset':                 'Сброс правки начисления',
  'payroll.cancellation_update':   'Правка начисления за отмену',
  'payroll.cancellation_reset':    'Сброс правки отмены',
  'payroll.rate_version_create':   'Новые ставки школы',
  'payroll.rate_version_update':   'Правка ставок школы',
  'payroll.rate_version_delete':   'Удаление ставок школы',
  'payroll.personal_rate_create':  'Новая персональная ставка',
  'payroll.personal_rate_update':  'Правка персональной ставки',
  'payroll.personal_rate_delete':  'Удаление персональной ставки',
  'payroll.personal_rate_deactivate': 'Выключение персональной ставки',
  'payroll.personal_rate_activate':   'Включение персональной ставки',
  'payroll.recalculate':           'Пересчёт зарплат',
  'account.create':                'Новая учётка',
  'account.update':                'Правка учётки',
  'account.delete':                'Учётка выключена',
  'account.reset_password':        'Сброс пароля',
  'account.reset_2fa':             'Сброс 2FA',
  'account.invite_create':         'Invite-ссылка',
  'account.invite_revoke':         'Отзыв invite',
  'account.invite_accept':         'Активация учётки',
  'account.twofa_enable':          'Включение 2FA',
  'account.twofa_disable':         'Выключение 2FA',
  'teacher.telegram_link':         'Привязка Telegram',
  'teacher.telegram_unlink':       'Отвязка Telegram',
  'plan.resync':                   'Починка плана',
  'changelog.revert':              'Откат',
  'eduson.import':                 'Импорт Eduson',
  'eduson.sync':                   'Обновление с прода',
  'account.twofa_setup':           'Настройка 2FA',
  'payment.refund':                'Возврат',
  'membership.transfer':           'Перевод в группу',
  'group.lesson_skip':             'Пропуск урока группы',
  'lesson.unpaid_skip':            'Неоплачиваемый пропуск',
  'extra_lesson.manual_create':    'Доп.урок вручную',
  'notifications.toggle':          'Рассылка вкл/выкл',
  'renewal.create':                'Новая сделка продления',
  'renewal.move':                  'Смена стадии продления',
  'renewal.reopen':                'Переоткрытие продления',
  'renewal.unfreeze':              'Возврат продления в работу',
  'renewal.comment':               'Комментарий к продлению',
  'renewal.outcome_date_update':   'Дата итога продления',
  'renewal.update':                'Правка продления',
  'renewal.stage_create':          'Новая стадия продлений',
  'renewal.stage_update':          'Правка стадии продлений',
  'renewal.stage_delete':          'Удаление стадии продлений',
  'renewal.stage_reorder':         'Порядок стадий продлений',
  'task.create':                   'Новая задача',
  'task.update':                   'Правка задачи',
  'task.delete':                   'Удаление задачи',
  'task.move':                     'Перенос задачи',
  'task.complete':                 'Закрытие задачи',
  'task.comment':                  'Комментарий к задаче',
  'task_board.create':             'Новая доска задач',
  'task_board.update':             'Правка доски задач',
  'task_board.delete':             'Удаление доски задач',
  'task_stage.create':             'Новый этап задач',
  'task_stage.update':             'Правка этапа задач',
  'task_stage.delete':             'Удаление этапа задач',
  'task_stage.reorder':            'Порядок этапов задач',
  'cancellation_reason.create':    'Новая причина отмены',
  'cancellation_reason.update':    'Изменение причины отмены',
  'cancellation_reason.delete':    'Удаление причины отмены',
  other:                           'Другое действие',
};

export const CHANGELOG_OPERATION_OPTIONS: { value: string; label: string }[] =
  Object.entries(CHANGELOG_OPERATION_LABELS).map(([value, label]) => ({ value, label }));

// ===== Changelog: сущности (registry.py → entity) =====

export const CHANGELOG_ENTITY_LABELS: Record<string, string> = {
  direction:      'Направление',
  teacher:        'Преподаватель',
  student:        'Ученик',
  discount:       'Скидка',
  account:        'Учётка',
  group:          'Группа',
  schedule_slot:  'Слот расписания',
  membership:     'Членство',
  planned_lesson: 'Плановое занятие',
  lesson:         'Урок',
  attendance:     'Посещаемость',
  payment:        'Оплата',
  payroll:        'Начисление',
  pay_rate_version:   'Ставки школы',
  pay_rate_direction: 'Ставка направления',
  personal_rate:      'Персональная ставка',
  personal_rate_term: 'Период персональной ставки',
  cancellation_pay:   'Начисление за отмену',
  absence_resolution: 'Пропуск (резолюция)',
  lesson_skip:    'Неоплачиваемый пропуск',
  cancellation_reason: 'Причина отмены',
  lesson_cancellation: 'Отмена урока',
};

export const CHANGELOG_ENTITY_OPTIONS: { value: string; label: string }[] =
  Object.entries(CHANGELOG_ENTITY_LABELS).map(([value, label]) => ({ value, label }));

// ===== Уведомления: типы, каналы, статусы =====
// Ключи — из apps/notifications/constants.py (KIND_/CHANNEL_/STATUS_CHOICES).

export const NOTIFICATION_KIND_LABELS: Record<string, string> = {
  morning_digest:      'Расписание на день',
  fill_digest:         'Не заполнены отчёты',
  makeup_assigned:     'Назначен доп.урок',
  makeup_changed:      'Изменён доп.урок',
  makeup_cancelled:    'Отменён доп.урок',
  lesson_moved:        'Перенос занятия',
  lesson_cancelled:    'Отмена занятия',
  substitute_assigned: 'Назначена замена',
  substitute_removed:  'Снята замена',
  lesson_unrecorded:   'Урок не записан',
  parent_day_before:   'Родителям: урок завтра',
  parent_hour_before:  'Родителям: урок через час',
};

export const NOTIFICATION_KIND_OPTIONS: { value: string; label: string }[] =
  Object.entries(NOTIFICATION_KIND_LABELS).map(([value, label]) => ({ value, label }));

export const NOTIFICATION_CHANNEL_LABELS: Record<string, string> = {
  dm:         'Личка',
  group_chat: 'Общий чат',
  email:      'Почта',
};

export const NOTIFICATION_CHANNEL_OPTIONS: { value: string; label: string }[] =
  Object.entries(NOTIFICATION_CHANNEL_LABELS).map(([value, label]) => ({ value, label }));

export const NOTIFICATION_STATUS_LABELS: Record<string, string> = {
  queued: 'В очереди',
  sent:   'Отправлено',
  failed: 'Не доставлено',
};

export const NOTIFICATION_STATUS_OPTIONS: { value: string; label: string }[] =
  Object.entries(NOTIFICATION_STATUS_LABELS).map(([value, label]) => ({ value, label }));

// ===== Продления: стадии воронки (fallback, если под рукой только key —
// основной источник истины — stage_label/label с бэка) =====

export const RENEWAL_STAGE_LABELS: Record<string, string> = {
  no_lesson_yet: 'Не было урока', lesson_1: 'Урок 1', lesson_2: 'Урок 2', lesson_3: 'Урок 3',
  awaiting_payment: 'Ждём оплату', awaiting_renewal: 'Ждём продление', thinking: 'Думает',
  frozen: 'Заморожен', ignoring: 'Игнорит', renewed: 'Продлён', churned: 'Ушёл',
};

// Причины ухода ученика (диалог закрытия сделки «Ушёл», reason_code сделки).
export const RENEWAL_LOST_REASON_LABELS: Record<string, string> = {
  price: 'Не устроила цена',
  schedule: 'Не подошло расписание',
  lost_interest: 'Потерял интерес',
  relocation: 'Переезд',
  other: 'Другое',
};

// ===== Состояние плана группы (GET /plan/health, ключи из apps/scheduling/health.py::CHECKS) =====

// Подписи проверок расхождения план↔занятия. 'duplicate_position_numbers' —
// восьмая причина блокировки, её считает только сам resync (repository.py,
// не входит в health.CHECKS), поэтому здесь, а не в CHECKS-списке проверок.
export const PLAN_HEALTH_CHECK_LABELS: Record<string, string> = {
  fact_without_position:       'Занятие не привязано ни к одной позиции курса',
  beyond_course:                'Позиция выходит за пределы длины курса',
  number_mismatch:              'Номер позиции не совпадает с номером занятия',
  date_mismatch:                'Плановая дата не совпадает с датой занятия',
  done_in_future:                'Отмечено проведённым, а дата ещё не наступила',
  collision:                    'Два занятия стоят в один день и час',
  duplicate_dates:              'Два занятия проведены в один день',
  duplicate_position_numbers:   'В плане несколько позиций с одним номером урока',
};

// Причина, по которой конкретному занятию не находится позиция при починке
// (resync.orphan_facts[].reason, apps/scheduling/repository.py::_plan_resync_changes).
export const PLAN_HEALTH_ORPHAN_REASON_LABELS: Record<string, string> = {
  no_position:            'В плане курса нет позиции с таким номером урока',
  locked_position:         'Позиция с этим номером занята и её нельзя трогать (перенесена/отменена)',
  duplicate_fact_number:   'Ещё одно занятие с тем же номером урока',
};

// ===== Русские числительные =====

/**
 * Согласование существительного с числом: `plural(2, 'ученик', 'ученика', 'учеников')`
 * → 'ученика'.
 *
 * Первое место в SPA, где число стоит вплотную к склоняемому слову (карточка
 * преподавателя: «2 ученика», «1 замена», «5 групп»). Без этого выходило
 * «2 учеников» и «1 замен» — мелочь, которую замечают все.
 */
export function plural(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(n) % 100;
  if (abs > 10 && abs < 20) return many;
  const last = abs % 10;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

// ===== Раздел «Задачи» (спека 2026-08-24) =====

export const TASK_PRIORITY_LABELS: Record<string, string> = {
  low:    'Низкий',
  normal: 'Обычный',
  high:   'Высокий',
};

// Варианты селектора «Срок» (TaskFilters.due). Значения — контракт бэка,
// подписи держим здесь же, где остальные словари раздела.
export const TASK_DUE_LABELS: Record<string, string> = {
  today:   'Сегодня',
  week:    'Эта неделя',
  overdue: 'Просроченные',
  none:    'Без срока',
};

export const TASK_RESOLUTION_LABELS: Record<string, string> = {
  done:       'Выполнено',
  cancelled:  'Отменено',
  irrelevant: 'Неактуально',
};

/**
 * Названия полей для фраз истории: «изменил(-а) <что> на <значение>».
 *
 * Винительный падеж, а не именительный: история в панели — не таблица «поле:
 * было → стало», а лента фраз, и «изменил(-а) Срок на 3 сентября» в неё не
 * встаёт. Бэкенд пишет в meta сырое {field, from, to} (см. taskboard/
 * services.py::update_task) — русский текст собирается здесь, чтобы
 * переименование ученика не переврало историю задним числом.
 */
export const TASK_FIELD_PHRASES: Record<string, string> = {
  title:        'заголовок',
  description:  'описание',
  due_date:     'срок',
  priority:     'приоритет',
  // Тип задачи как свойство убран (ТЗ 2026-08-28), подпись оставлена ради
  // старых записей ленты: новых правок этого поля не будет, а без подписи
  // уже накопленные строки истории выродились бы в «изменил(-а) task_type_id».
  task_type_id: 'тип задачи',
  student_id:   'ученика',
  group_id:     'группу',
};

// Коды конфликтов раздела приходят с бэка в теле {error: '...'} и попадают
// в ApiError.message — так же, как их читают продления.
export const TASK_CONFLICT_LABELS: Record<string, string> = {
  has_tasks:                    'В этой воронке или стадии есть задачи — сначала перенесите или закройте их',
  last_stage_of_category:       'Это последняя стадия своего вида: без неё воронка перестанет работать',
  duplicate_name:               'Такое название уже занято',
  duplicate_label:              'Такое название уже занято',
  stages_from_different_boards: 'Стадии разных воронок нельзя переставлять вместе',
  incomplete_stage_set:         'Передан неполный набор стадий воронки',
  unknown_stage:                'Такой стадии больше не существует — обновите страницу',
};

// ===== Отзывы о занятиях (раздел «Отзывы», вкладка группы) =====

export const FEEDBACK_LABELS = {
  title: 'Отзывы',
  subtitle: 'Обратная связь преподавателей после каждого занятия',
  tab: 'Отзывы',
  colDate: 'Дата урока',
  colGroup: 'Группа',
  colTeacher: 'Преподаватель',
  colUnderstanding: 'Понимание',
  colInterest: 'Интерес',
  colTech: 'Тех. проблемы',
  colComment: 'Комментарий',
  colRecord: 'Запись',
  colSubmitted: 'Отправлен',
  techYes: 'Были',
  techNo: 'Нет',
  lessonDeleted: 'урок удалён',
  notHeld: 'не состоялся',
  openRecord: 'Открыть запись',
  dateFrom: 'Дата: с',
  dateTo: 'Дата: по',
  rangeInverted: '«Дата: с» позже «Дата: по» — период не применён.',
  errorTitle: 'Не удалось загрузить отзывы',
  errorHint: 'Это сбой загрузки, а не отсутствие отзывов. Попробуйте ещё раз.',
  retry: 'Повторить',
} as const;

// ===== Настройки: подписи вкладок =====

export const SETTINGS_TAB_LABELS = {
  motivation: 'Мотивация',
} as const;

// ===== Общий свёрнутый текст (ClampText) =====

export const CLAMP_LABELS = {
  expand: 'Показать полностью',
  collapse: 'Свернуть',
} as const;

// ===== Отмены уроков: причины, форма отмены, вкладка группы =====

export const CANCELLATION_SIDE_LABELS: Record<CancellationSide, string> = {
  teacher: 'Преподаватель',
  client: 'Клиент',
};

export const CANCELLATION_LABELS = {
  tab: 'Отмены уроков',
  title: 'Отмены уроков',
  side: 'С чьей стороны отмена',
  reason: 'Причина',
  otherReason: 'Другая причина',
  otherText: 'Опишите причину',
  notifiedOn: 'Когда сообщили об отмене',
  addReason: 'Добавить причину',
  reasonName: 'Название причины',
  compensation: 'Компенсация',
  noCompensation: 'компенсации нет',
  empty: 'Отмен пока не было',
  errorTitle: 'Не удалось загрузить отмены',
  errorHint: 'Это сбой загрузки, а не отсутствие отмен. Попробуйте ещё раз.',
  retry: 'Повторить',
  colLessonDate: 'Занятие',
  colSide: 'Сторона',
  colReason: 'Причина',
  // Диалог отмены занятия в плане группы.
  dialogTitle: 'Отменить занятие',
  lesson: 'Занятие',
  pickLesson: 'Выберите занятие',
  pickSide: 'Выберите сторону',
  pickReason: 'Выберите причину',
  submit: 'Отменить занятие',
  success: 'Занятие отменено',
  reasonsLoading: 'Загрузка…',
  reasonsError: 'Не удалось загрузить причины отмены',
  changed: 'Занятие изменилось, план обновлён — выберите занятие заново',
  otherPlaceholder: 'Что именно произошло',
  // Вкладка группы: колонка «Отменил» (кто и когда внёс отмену).
  colCancelledBy: 'Отменил',
  // Настройки → Отмены уроков.
  settingsLead: 'Причины, из которых выбирают при отмене занятия. От переключателя «Компенсация» зависит, платят ли преподавателю за отменённый урок.',
  edit: 'Изменить',
  remove: 'Удалить',
  rowActions: 'Действия с причиной',
  save: 'Сохранить',
  saving: 'Сохраняем…',
  cancel: 'Отмена',
  newReasonTitle: 'Новая причина',
  editReasonTitle: 'Изменить причину',
  deleteTitle: 'Удалить причину?',
  deleteMessage: (name: string) =>
    `Причина «${name}» исчезнет из списка. Уже внесённые отмены с этой причиной не изменятся.`,
  settingsErrorTitle: 'Не удалось загрузить причины отмены',
  settingsErrorHint: 'Это сбой загрузки, а не отсутствие причин. Попробуйте ещё раз.',
  toggleFailed: 'Не удалось изменить компенсацию',
  deleteFailed: 'Не удалось удалить причину',
} as const;

// ===== Настройки → Мотивация =====

export const CANCEL_MODE_LABELS: Record<CancelMode, string> = {
  percent: '% от ставки',
  fixed:   'Фикс, ₽',
};

export const PENALTY_MODE_LABELS: Record<PenaltyMode, string> = {
  per_lesson:  'за урок',
  per_student: 'за каждого пришедшего',
};

export const PERSONAL_RATE_KIND_LABELS: Record<PersonalRateKind, string> = {
  fixed:        'Вместо ставки',
  plus_fixed:   'Надбавка, ₽',
  plus_percent: 'Надбавка, %',
};
