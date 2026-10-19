"""Services renewals — тонкий слой между views и repository/engine."""
from __future__ import annotations

from apps.renewals import repository


def board(filters: dict | None = None) -> dict:
    return repository.board(filters)


def column_cards(stage_id: int, offset: int, filters: dict | None = None) -> dict:
    return repository.column_cards(stage_id, offset, filters)


def list_deals(**kwargs) -> dict:
    return repository.list_deals(**kwargs)


def get_deal(deal_id: int) -> dict | None:
    return repository.deal_computed(deal_id)


def move_deal(deal_id, to_stage_id, reason_code, author_id, frozen_until_month=None,
              allow_nonpositive_balance=False):
    return repository.move_deal(deal_id, to_stage_id, reason_code, author_id,
                                frozen_until_month=frozen_until_month,
                                allow_nonpositive_balance=allow_nonpositive_balance)


def list_unassigned() -> list[dict]:
    """Сводка «Ученики без сделок» — новички: активный membership, сделок не было."""
    return repository.students_without_deal()


def count_unassigned() -> int:
    """Число для бейджа «Без сделок (N)» — без выгрузки самого списка."""
    return repository.count_students_without_deal()


def create_deal(student_id: int, author_id: int | None) -> dict | str | None:
    """
    Ручное создание сделки учеником сводки: None — ученика нет; 'exists' —
    открытая сделка уже есть; 'cycle_taken' — расчётный цикл уже закрыт сделкой;
    dict — созданная сделка.

    Номер цикла — расчётный от общей истории (cycle.open_cycle_no: ровно на
    рубеже 4 уроков открыт ТОТ ЖЕ цикл, решение по нему ещё не принято, поэтому
    сделка встанет на «Ждём продление»).

    Если расчётный номер уже занят ЗАКРЫТОЙ сделкой (вернувшийся после «Ушёл»),
    создавать нечего: перешагнув номер вперёд, мы завели бы сделку впереди
    посещаемости — прогресс `attended − (cycle_no−1)×4` ушёл бы в минус, и
    карточка показала бы «Не было урока» ученику с полной историей уроков.
    Правильный путь — переоткрыть закрытую сделку из её карточки: сохранится её
    номер цикла и прогресс (см. docs/renewals-user-guide.md, §9). Тот же класс
    поломки, что закрыл engine._assert_reopenable — регрессия 2026-08-25.

    ⚠️ НЕ УДАЛЯТЬ как недостижимый код. Сегодня из UI сюда таких учеников не
    приводят: сводка «Без сделок» отбирает только новичков (`NOT EXISTS
    renewal_deal`, repository._UNASSIGNED_SOURCE), а у ученика без единой сделки
    расчётный номер свободен по определению. Но правило сводки живёт в ДРУГОМ
    запросе и ничем с этой проверкой не связано: до 27.07.2026 сводка отбирала
    всех без ОТКРЫТОЙ сделки, и путь был вполне живым. Сузили — закрылся молча,
    расширят обратно — так же молча откроется. Здесь единственное место, где
    инвариант «номер цикла не убегает вперёд посещаемости» проверяется на входе,
    а эндпоинт открыт любому менеджеру помимо диалога сводки.
    """
    from apps.renewals import cycle, engine
    from apps.renewals.models import RenewalDeal
    from apps.students.models import Student

    if not Student.objects.filter(id=student_id).exists():
        return None
    if RenewalDeal.objects.filter(student_id=student_id, outcome_at__isnull=True).exists():
        return 'exists'

    min_cycle_no = cycle.open_cycle_no(engine._attended_total(student_id))
    cycle_no = engine.next_open_cycle_no(student_id, min_cycle_no)
    if cycle_no != min_cycle_no:
        # Открытых сделок здесь уже нет ('exists' выше), значит расчётный номер
        # держит закрытая сделка — её и надо переоткрывать, а не спавнить новую.
        # Про «недостижимо из UI» — см. предупреждение в докстринге.
        return 'cycle_taken'

    deal = engine.ensure_deal(student_id, cycle_no)
    engine.sync_lesson_stage_safe(student_id)  # сразу в актуальную авто-стадию
    return repository.deal_computed(deal.id)


def reopen_deal(deal_id: int, author_id: int | None) -> dict | str | None:
    """None — сделки нет; 'not_closed' — она и так открыта; dict — переоткрыта."""
    from apps.renewals import engine
    from apps.renewals.models import RenewalDeal
    if not RenewalDeal.objects.filter(id=deal_id).exists():
        return None
    deal = engine.reopen_deal(deal_id, author_id=author_id)
    if deal is None:
        return 'not_closed'
    return repository.deal_computed(deal_id)


def set_outcome_date(deal_id: int, outcome_date, author_id: int | None) -> dict | str | None:
    """None — сделки нет; 'not_closed' — она ещё открыта; dict — дату переставили."""
    return repository.set_outcome_date(deal_id, outcome_date, author_id)


def unfreeze_deal(deal_id: int, author_id: int | None) -> dict | str | None:
    """None — сделки нет; 'not_frozen' — она не на стадии-паузе; dict — вернули в работу.

    Имя (и URL /unfreeze) историческое: действие появилось для заморозки, а с
    2026-08-06 работает для любой стадии с allow_mid_cycle. Контракт не
    переименовываем — он уже в проде и на фронте.
    """
    from apps.renewals import engine
    from apps.renewals.models import RenewalDeal
    if not RenewalDeal.objects.filter(id=deal_id).exists():
        return None
    deal = engine.return_to_work(deal_id, author_id=author_id)
    if deal is None:
        return 'not_frozen'
    return repository.deal_computed(deal_id)


def list_assignees() -> list[dict]:
    """Кандидаты в ответственные по сделкам: активные manager/admin/superadmin."""
    from apps.accounts.models import Account
    return list(Account.objects
                .filter(role__in=['manager', 'admin', 'superadmin'], is_active=True)
                .order_by('full_name').values('id', 'full_name'))


def patch_deal(deal_id, data):
    return repository.patch_deal(deal_id, data)


def add_comment(deal_id, body, author_id):
    return repository.add_comment(deal_id, body, author_id)


def list_activity(deal_id):
    return repository.list_activity(deal_id)


def list_stages() -> list[dict]:
    return repository.list_stages()


def create_stage(data: dict) -> dict:
    return repository.create_stage(data)


def update_stage(stage_id: int, data: dict) -> dict | None:
    return repository.update_stage(stage_id, data)


def delete_stage(stage_id: int) -> str:
    return repository.delete_stage(stage_id)


def reorder_stages(order: list) -> list[dict]:
    return repository.reorder_stages(order)


def analytics_funnel(group_by: str | None = None) -> dict:
    from apps.renewals import analytics
    return analytics.funnel(group_by)
