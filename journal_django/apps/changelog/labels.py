"""
Метки операций журнала: (HTTP-метод, url) → машинный ключ операции.

Русские названия — на фронте (lib/labels.ts). Ключ 'other' — fallback
для незамапленных мутаций: журнал остаётся читаемым, событие не теряется.
Порядок правил важен: более специфичные пути выше.
"""
from __future__ import annotations

import re

# (method, compiled regex, operation)
RULES: list[tuple[str, re.Pattern, str]] = [
    # scheduling (план занятий) — до generic groups-правил
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/generate$'), 'plan.generate'),
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/permanent-change$'), 'plan.permanent_change'),
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/change-teacher-permanent$'), 'plan.change_teacher_permanent'),
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/resync$'), 'plan.resync'),
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/extra$'), 'plan.extra'),
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/\d+/reschedule$'), 'plan.reschedule'),
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/\d+/change-teacher$'), 'plan.change_teacher'),
    ('POST', re.compile(r'^/api/admin/groups/\d+/plan/\d+/cancel$'), 'plan.cancel'),
    # groups
    ('POST', re.compile(r'^/api/admin/groups/\d+/schedule-change$'), 'group.schedule_change'),
    ('PATCH', re.compile(r'^/api/admin/groups/\d+/lesson-skips$'), 'group.lesson_skip'),
    ('POST', re.compile(r'^/api/admin/groups$'), 'group.create'),
    ('PATCH', re.compile(r'^/api/admin/groups/\d+$'), 'group.update'),
    ('DELETE', re.compile(r'^/api/admin/groups/\d+$'), 'group.delete'),
    # справочники
    ('POST', re.compile(r'^/api/admin/directions$'), 'direction.create'),
    ('PATCH', re.compile(r'^/api/admin/directions/\d+$'), 'direction.update'),
    ('DELETE', re.compile(r'^/api/admin/directions/\d+$'), 'direction.delete'),
    ('POST', re.compile(r'^/api/admin/teachers$'), 'teacher.create'),
    ('PATCH', re.compile(r'^/api/admin/teachers/\d+$'), 'teacher.update'),
    ('DELETE', re.compile(r'^/api/admin/teachers/\d+$'), 'teacher.delete'),
    # Telegram-уведомления: привязка/отвязка аккаунта преподавателя. Эндпоинты
    # появятся позже (см. план фичи), правила заводятся заранее.
    ('POST', re.compile(r'^/api/admin/notifications/toggle$'), 'notifications.toggle'),
    ('POST', re.compile(r'^/api/admin/teachers/\d+/telegram$'), 'teacher.telegram_link'),
    ('DELETE', re.compile(r'^/api/admin/teachers/\d+/telegram$'), 'teacher.telegram_unlink'),
    ('POST', re.compile(r'^/api/admin/students$'), 'student.create'),
    ('PATCH', re.compile(r'^/api/admin/students/\d+/manager$'), 'student.manager_update'),
    ('PATCH', re.compile(r'^/api/admin/students/\d+$'), 'student.update'),
    # LEGACY: эндпоинт удалён вместе со статусом not_enrolled (soft-delete ученика).
    # Правило оставлено намеренно: операция резолвится при ЧТЕНИИ журнала из
    # сохранённых method+url (repository._operation_of), поэтому без него прошлые
    # записи «Ученик в архив» схлопнулись бы в 'other' и потерялись бы в фильтре.
    ('DELETE', re.compile(r'^/api/admin/students/\d+$'), 'student.delete'),
    # LEGACY: статусы ученика удалены вместе с эндпоинтами смены статуса и
    # разморозки (спека 2026-07-25). Правила оставлены по той же причине, что
    # student.delete выше: в БД есть исторические события заморозки/отказа, и без
    # правил они схлопнулись бы в 'other'.
    ('POST', re.compile(r'^/api/admin/students/\d+/status$'), 'student.status'),
    ('POST', re.compile(r'^/api/admin/students/\d+/resume$'), 'student.resume'),
    ('POST', re.compile(r'^/api/admin/discounts$'), 'discount.create'),
    ('PATCH', re.compile(r'^/api/admin/discounts/\d+$'), 'discount.update'),
    ('DELETE', re.compile(r'^/api/admin/discounts/\d+$'), 'discount.delete'),
    # memberships
    # Эндпоинт /memberships/place снят, но правило остаётся: операция резолвится
    # ИЗ СОХРАНЁННОГО url при чтении ленты — без него уже записанные события
    # потеряли бы описание и откат (см. summary.py, ветка 'membership.place').
    ('POST', re.compile(r'^/api/admin/memberships/place$'), 'membership.place'),
    ('POST', re.compile(r'^/api/admin/memberships/\d+/transfer$'), 'membership.transfer'),
    ('POST', re.compile(r'^/api/admin/memberships$'), 'membership.create'),
    ('PATCH', re.compile(r'^/api/admin/memberships/\d+$'), 'membership.update'),
    ('DELETE', re.compile(r'^/api/admin/memberships/\d+$'), 'membership.delete'),
    # payments (immutable: только create/delete)
    ('POST', re.compile(r'^/api/admin/payments$'), 'payment.create'),
    ('DELETE', re.compile(r'^/api/admin/payments/\d+$'), 'payment.delete'),
    ('POST', re.compile(r'^/api/admin/students/\d+/refund$'), 'payment.refund'),
    # lessons
    ('PATCH', re.compile(r'^/api/admin/lessons/\d+/attendance/\d+$'), 'lesson.attendance_update'),
    ('PATCH', re.compile(r'^/api/admin/lessons/\d+/unpaid-skip/\d+$'), 'lesson.unpaid_skip'),
    ('POST', re.compile(r'^/api/admin/lessons$'), 'lesson.create'),
    ('PATCH', re.compile(r'^/api/admin/lessons/\d+$'), 'lesson.update'),
    ('DELETE', re.compile(r'^/api/admin/lessons/\d+$'), 'lesson.delete'),
    # extra_lessons (доп.уроки)
    ('POST', re.compile(r'^/api/admin/extra-lessons$'), 'extra_lesson.create'),
    ('POST', re.compile(r'^/api/admin/extra-lessons/manual$'), 'extra_lesson.manual_create'),
    ('POST', re.compile(r'^/api/admin/extra-lessons/\d+/cancel$'), 'extra_lesson.cancel'),
    ('POST', re.compile(r'^/api/admin/extra-lessons/\d+/burn$'), 'extra_lesson.burn'),
    # Действие «закрыть без денег» снято (эндпоинт /waive удалён), но правило
    # остаётся: resolve_operation резолвит операцию из СОХРАНЁННОГО url при чтении
    # ленты, а в БД есть исторические waive-события — без правила они потеряли бы
    # описание. См. тот же приём для membership.place.
    ('POST', re.compile(r'^/api/admin/extra-lessons/\d+/waive$'), 'extra_lesson.waive'),
    ('DELETE', re.compile(r'^/api/admin/extra-lessons/\d+$'), 'extra_lesson.delete'),
    ('POST', re.compile(r'^/api/extra-lessons/\d+/record$'), 'extra_lesson.record'),
    # payroll
    ('PATCH', re.compile(r'^/api/admin/payroll/\d+$'), 'payroll.update'),
    ('POST', re.compile(r'^/api/admin/payroll/\d+/reset$'), 'payroll.reset'),
    ('PATCH', re.compile(r'^/api/admin/payroll/cancellations/\d+$'), 'payroll.cancellation_update'),
    ('POST', re.compile(r'^/api/admin/payroll/cancellations/\d+/reset$'),
     'payroll.cancellation_reset'),
    # «Настройки → Мотивация»: каждая мутация несёт и пересчёт начислений.
    ('POST', re.compile(r'^/api/admin/payroll/rate-versions$'), 'payroll.rate_version_create'),
    ('PATCH', re.compile(r'^/api/admin/payroll/rate-versions/\d+$'), 'payroll.rate_version_update'),
    ('DELETE', re.compile(r'^/api/admin/payroll/rate-versions/\d+$'),
     'payroll.rate_version_delete'),
    ('POST', re.compile(r'^/api/admin/payroll/personal-rates$'), 'payroll.personal_rate_create'),
    ('PATCH', re.compile(r'^/api/admin/payroll/personal-rates/\d+$'),
     'payroll.personal_rate_update'),
    ('DELETE', re.compile(r'^/api/admin/payroll/personal-rates/\d+$'),
     'payroll.personal_rate_delete'),
    ('POST', re.compile(r'^/api/admin/payroll/personal-rates/\d+/deactivate$'),
     'payroll.personal_rate_deactivate'),
    ('POST', re.compile(r'^/api/admin/payroll/personal-rates/\d+/activate$'),
     'payroll.personal_rate_activate'),
    # accounts
    ('POST', re.compile(r'^/api/admin/accounts/\d+/reset-password$'), 'account.reset_password'),
    ('POST', re.compile(r'^/api/admin/accounts/\d+/reset-2fa$'), 'account.reset_2fa'),
    ('POST', re.compile(r'^/api/admin/accounts/\d+/invite/revoke$'), 'account.invite_revoke'),
    ('POST', re.compile(r'^/api/admin/accounts/\d+/invite$'), 'account.invite_create'),
    ('POST', re.compile(r'^/api/admin/accounts$'), 'account.create'),
    ('PATCH', re.compile(r'^/api/admin/accounts/\d+$'), 'account.update'),
    ('DELETE', re.compile(r'^/api/admin/accounts/\d+$'), 'account.delete'),
    # renewals (стадии-справочник — до generic deal-правил)
    ('POST', re.compile(r'^/api/admin/renewals/stages/reorder$'), 'renewal.stage_reorder'),
    ('POST', re.compile(r'^/api/admin/renewals/stages$'), 'renewal.stage_create'),
    ('PATCH', re.compile(r'^/api/admin/renewals/stages/\d+$'), 'renewal.stage_update'),
    ('DELETE', re.compile(r'^/api/admin/renewals/stages/\d+$'), 'renewal.stage_delete'),
    ('POST', re.compile(r'^/api/admin/renewals$'), 'renewal.create'),
    ('POST', re.compile(r'^/api/admin/renewals/\d+/move$'), 'renewal.move'),
    ('POST', re.compile(r'^/api/admin/renewals/\d+/reopen$'), 'renewal.reopen'),
    ('POST', re.compile(r'^/api/admin/renewals/\d+/unfreeze$'), 'renewal.unfreeze'),
    ('POST', re.compile(r'^/api/admin/renewals/\d+/comment$'), 'renewal.comment'),
    ('PATCH', re.compile(r'^/api/admin/renewals/\d+/outcome-date$'),
     'renewal.outcome_date_update'),
    ('PATCH', re.compile(r'^/api/admin/renewals/\d+$'), 'renewal.update'),
    # Задачи (спека 2026-08-24). Специфичные пути — до /<pk>-правил.
    ('POST', re.compile(r'^/api/admin/tasks/\d+/move$'), 'task.move'),
    ('POST', re.compile(r'^/api/admin/tasks/\d+/complete$'), 'task.complete'),
    ('POST', re.compile(r'^/api/admin/tasks/\d+/comment$'), 'task.comment'),
    ('POST', re.compile(r'^/api/admin/tasks/boards$'), 'task_board.create'),
    ('PATCH', re.compile(r'^/api/admin/tasks/boards/\d+$'), 'task_board.update'),
    ('DELETE', re.compile(r'^/api/admin/tasks/boards/\d+$'), 'task_board.delete'),
    ('POST', re.compile(r'^/api/admin/tasks/boards/\d+/stages$'), 'task_stage.create'),
    ('POST', re.compile(r'^/api/admin/tasks/stages/reorder$'), 'task_stage.reorder'),
    ('PATCH', re.compile(r'^/api/admin/tasks/stages/\d+$'), 'task_stage.update'),
    ('DELETE', re.compile(r'^/api/admin/tasks/stages/\d+$'), 'task_stage.delete'),
    ('POST', re.compile(r'^/api/admin/tasks$'), 'task.create'),
    ('PATCH', re.compile(r'^/api/admin/tasks/\d+$'), 'task.update'),
    ('DELETE', re.compile(r'^/api/admin/tasks/\d+$'), 'task.delete'),
    # teacher SPA
    ('POST', re.compile(r'^/api/submitLesson$'), 'lesson.submit'),
    # auth-мутации данных (2FA-поля Account меняет сам пользователь)
    ('POST', re.compile(r'^/api/auth/2fa/setup$'), 'account.twofa_setup'),
    ('POST', re.compile(r'^/api/auth/2fa/enable$'), 'account.twofa_enable'),
    ('POST', re.compile(r'^/api/auth/2fa/disable$'), 'account.twofa_disable'),
    ('POST', re.compile(r'^/api/auth/invite/accept$'), 'account.invite_accept'),
    # Разовый перенос Eduson (manage.py import_eduson). Операцию команда ставит в
    # metadata сама; правило нужно фильтру ленты — он ищет операцию по method+url.
    ('COMMAND', re.compile(r'^manual/import-eduson$'), 'eduson.import'),
    # Мягкое обновление с прода (manage.py sync_eduson): группы и ученики — с журналом,
    # замена уроков — без (apps.eduson_import.sync).
    ('COMMAND', re.compile(r'^manual/sync-eduson$'), 'eduson.sync'),
    # Пересчёт зарплат (manage.py recalculate_payroll) — контекст ставит команда.
    ('COMMAND', re.compile(r'^manual/recalculate-payroll$'), 'payroll.recalculate'),
]

FALLBACK = 'other'


def resolve_operation(method: str, url: str) -> str:
    """Ключ операции по методу и пути. У вызывающего metadata['operation']
    имеет приоритет (например 'changelog.revert' проставляется сервисом)."""
    for rule_method, pattern, operation in RULES:
        if method == rule_method and pattern.match(url):
            return operation
    return FALLBACK


def is_motivation_operation(operation: str) -> bool:
    """Операция зарплаты и «Мотивации» (ставки, начисления, их ручные правки).

    Мотивация сотрудников конфиденциальна и управляется только superadmin
    (решение владельца 2026-10-23): admin такие операции в ленте видит
    формально, но не откатывает."""
    return operation.startswith('payroll.')


def rule_for_operation(operation: str):
    """Обратный поиск для фильтра ленты: operation → (method, regex) или None."""
    for rule_method, pattern, rule_operation in RULES:
        if rule_operation == operation:
            return rule_method, pattern
    return None
