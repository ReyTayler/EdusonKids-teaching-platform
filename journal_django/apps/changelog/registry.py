"""
Реестр трекаемых моделей журнала изменений.

Единственный источник знания «какие модели в журнале, как их зовут в API,
можно ли откатывать, чем идентифицируется строка и в каком порядке
восстанавливать по FK».

topo-порядок: родители раньше детей (для re-insert при откате delete).
identity: attname-поля, однозначно идентифицирующие строку. По умолчанию
('id',); у lesson_attendance реальный PK составной (lesson_id, student_id) —
Django-модель помечает lesson как primary_key, поэтому pgh_obj_id там
НЕ уникален per-row и для отката используется identity.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps


@dataclass(frozen=True)
class TrackedModel:
    entity: str                        # ключ сущности для API/фронта
    revertable: bool
    topo: int                          # меньше = раньше вставлять при восстановлении
    identity: tuple[str, ...] = ('id',)


# Порядок topo: справочники → группы/ученики → членства/план → уроки → факты.
TRACKED: dict[str, TrackedModel] = {
    'directions.Direction':           TrackedModel('direction', True, 10),
    'teachers.Teacher':               TrackedModel('teacher', True, 10),
    'students.Student':               TrackedModel('student', True, 10),
    'discounts.Discount':             TrackedModel('discount', True, 10),
    'accounts.Account':               TrackedModel('account', False, 15),
    # Привязка «преподаватель ↔ Telegram-аккаунт» — не доменные данные, а канал
    # связи. Откат смысла не имеет: восстановление удалённой строки не вернёт
    # согласие человека получать сообщения, поэтому revertable=False.
    # Общешкольный выключатель рассылки: откат бессмысленен (это не данные,
    # а состояние сервиса), но кто и когда выключил — знать нужно.
    'notifications.NotificationSettings': TrackedModel('notification_settings', False, 15),
    'notifications.TelegramRecipient': TrackedModel('telegram_recipient', False, 15),
    'groups.Group':                   TrackedModel('group', True, 20),
    'groups.GroupScheduleSlot':       TrackedModel('schedule_slot', True, 30),
    'memberships.GroupMembership':    TrackedModel('membership', True, 30),
    'scheduling.PlannedLesson':       TrackedModel('planned_lesson', True, 30),
    'lessons.Lesson':                 TrackedModel('lesson', True, 40),
    'lessons.LessonAttendance':       TrackedModel('attendance', True, 50,
                                                   identity=('lesson_id', 'student_id')),
    # Пометка «неоплачиваемый пропуск» на слот. Трекинг добавлен, когда действие
    # перестали писать в журнал ИБ (log_event) — иначе следа не осталось бы вовсе.
    'lessons.LessonSkip':             TrackedModel('lesson_skip', True, 50),
    # Пер-ученик модель раздела доп.уроков (заменила групповые
    # ExtraLessonAssignment+ExtraLessonParticipant, удалённые на cutover Фазы 1a).
    # @pghistory.track на модели требует записи в реестре — иначе
    # test_registry_covers_all_tracked_models падает.
    'extra_lessons.AbsenceResolution':      TrackedModel('absence_resolution', True, 45),
    # Отзыв преподавателя по уроку (Eduson). topo после групп (20) и уроков (40):
    # при откате удаления сначала встают группа и урок, потом сам отзыв.
    'feedback.LessonFeedback':        TrackedModel('lesson_feedback', True, 50),
    'payments.Payment':               TrackedModel('payment', True, 50),
    'payroll.Payroll':                TrackedModel('payroll', True, 50),
    # renewals: справочники воронки → сделки → активность (лог, не откатываем)
    'renewals.RenewalPipeline':       TrackedModel('renewal_pipeline', True, 20),
    'renewals.RenewalStage':          TrackedModel('renewal_stage', True, 25),
    'renewals.RenewalDeal':           TrackedModel('renewal_deal', True, 35),
    'renewals.RenewalActivity':       TrackedModel('renewal_activity', False, 55),
    # Раздел «Задачи» (спека 2026-08-24). Лента активности неоткатываема —
    # это лог, восстанавливать в нём нечего.
    'taskboard.TaskBoard':    TrackedModel('task_board', True, 10),
    'taskboard.TaskStage':    TrackedModel('task_stage', True, 20),
    # Откат удаления карточки восстановит её поля, но НЕ исполнителей: связь m2m
    # идёт через автогенерируемую task_assignee, которую pghistory не трекает, а
    # revert восстанавливает только собственные поля модели. Смена набора
    # отдельно пишется в TaskActivity (kind='assign').
    'taskboard.Task':         TrackedModel('task', True, 30),
    'taskboard.TaskActivity': TrackedModel('task_activity', False, 40),
}


def event_model(model_label: str):
    """'groups.Group' → класс GroupEvent (авто-генерируется pghistory)."""
    app_label, model_name = model_label.split('.')
    return apps.get_model(app_label, f'{model_name}Event')


def tracked_model(model_label: str):
    """'groups.Group' → класс Group."""
    app_label, model_name = model_label.split('.')
    return apps.get_model(app_label, model_name)


def entity_of(model_label: str) -> str | None:
    """'groups.Group' → 'group'; None для нетрекаемых меток."""
    cfg = TRACKED.get(model_label)
    return cfg.entity if cfg else None


def model_label_for_entity(entity: str) -> str | None:
    """'group' → 'groups.Group'; None, если сущность неизвестна."""
    return next(
        (ml for ml, cfg in TRACKED.items() if cfg.entity == entity),
        None,
    )
