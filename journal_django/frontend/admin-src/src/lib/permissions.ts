export type Role = 'teacher' | 'manager' | 'admin' | 'superadmin';

const isSuper = (r?: Role | null) => r === 'superadmin';
const isAdminUp = (r?: Role | null) => r === 'admin' || r === 'superadmin';

// Разделы (видимость навигации / доступ к роуту)
export const canSeeAccounts = isSuper;
export const canSeeAudit = isSuper;
export const canSeePayroll = isSuper;
export const canSeeChangelog = isAdminUp; // журнал изменений — только admin/superadmin (не manager)
export const canSeeSync = isSuper;
export const canSeeArchive = isSuper;
// Починка плана группы (/plan/health, /plan/resync) — только суперадмин: бэк
// сам зажимает это IsSuperAdmin (разбор рассогласований план↔факт — операция
// уровня владельца системы, см. apps/scheduling/views.py::GroupPlanHealthView).
export const canFixPlan = isSuper;

// Архивация / разархивация сущностей (кнопки на detail-страницах, чекбокс active
// в формах). Только суперадмин — включая группы, где обычная правка доступна
// админам/менеджерам, а (раз)архивация — нет.
export const canArchiveEntities = isSuper;

// Операции над сущностями (write-кнопки)
export const canWriteTeachers = isSuper;
export const canWriteDirections = isSuper;
export const canWriteSubscriptions = isSuper; // абонементы + скидки
export const canWriteLessons = isAdminUp;     // CRUD урока + посещаемость
// «Записать занятие в долг» — снять блок по отрицательному балансу ученика.
// Только суперадмин: это обход финансового гарда, а не обычная правка. Бэк
// зажимает то же самое (apps/lessons/views.py::_reject_debt_override → 403).
export const canRecordLessonInDebt = isSuper;
// Оплаты: менеджер их видит, но не вносит и не удаляет — деньги заводит
// админ/суперадмин (решение 2026-07-28). Бэк: ReadStaffWriteAdmin на
// /api/admin/payments. Возврат средств менеджеру закрыт отдельно (IsAdminOrSuperAdmin).
export const canWritePayments = isAdminUp;
// Откат проведённого доп.урока и откат сгорания: обе операции возвращают урок на
// баланс ученика и снимают зарплату преподавателю — значит это деньги, и правит их
// админ/суперадмин (решение 2026-07-30). Менеджер кнопку видит, но она неактивна.
// Бэк: ReadStaffWriteAdmin на DELETE /api/admin/extra-lessons/:id.
export const canRollbackExtraLesson = isAdminUp;
// Полное удаление заявки на доп.урок в статусе «Ждёт решения». Денег за ней нет,
// но запись уходит из БД безвозвратно (восстановить можно только через «Журнал
// изменений»), поэтому право то же, что на откат: админ/суперадмин. Бэк — тот же
// DELETE /api/admin/extra-lessons/:id под ReadStaffWriteAdmin.
export const canDeleteExtraLessonRequest = isAdminUp;
export const canSeeLessonPayroll = isSuper;   // зарплата за урок
export const canRevertChangelog = isAdminUp;
export const canWriteRenewalStages = isSuper; // конфиг стадий воронки продлений (Фаза 6)
// Воронки и стадии задач. Права админа и суперадмина уравнены (решение
// заказчика 2026-08-27); бэк — ReadStaffWriteAdmin на пяти вьюхах воронок
// и стадий. Менеджер ведёт задачи внутри готовой структуры, но не правит её.
export const canWriteTaskStages = isAdminUp;
// Физическое удаление задачи. Штатный способ убрать задачу — закрыть её с
// результатом «неактуально»; DELETE стирает карточку вместе с лентой и нужен
// только для явного мусора. Бэк: IsAdminOrSuperAdmin на DELETE
// /api/admin/tasks/:id (apps/taskboard/views.py::TaskDetailView.get_permissions).
export const canDeleteTask = isAdminUp;
// Правка даты закрытия сделки задним числом. Дата закрытия = месяц, в который
// аналитика и «Переходимость» относят продление или уход, поэтому это правка
// отчётности, а не ведение сделки. Менеджеру закрыто (решение 2026-08-06).
// Бэк: IsAdminOrSuperAdmin на PATCH /api/admin/renewals/:id/outcome-date.
export const canEditRenewalOutcomeDate = isAdminUp;
export const canDeleteStudentComments = isAdminUp; // удаление комментария к ученику
// Вкладка «Уроки» на странице группы (сетка занятий + редактор урока). Менеджеру
// не нужна — уроки он всё равно не правит (canWriteLessons), а вкладка только
// шумит (решение 2026-07-28). Набор группы («Ученики») менеджеру доступен.
export const canSeeGroupLessonsTab = isAdminUp;
export const canWriteStudentManager = isAdminUp; // назначение ответственного менеджера ученику
// Привязка Telegram-аккаунта преподавателю. Менеджер поле видит (чтобы понимать,
// дойдут ли до преподавателя напоминания), но не меняет. Бэк:
// ReadStaffWriteAdmin на POST/DELETE /api/admin/teachers/:id/telegram.
export const canWriteTeacherTelegram = isAdminUp;
