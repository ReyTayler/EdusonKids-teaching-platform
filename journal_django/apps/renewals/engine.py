"""
Движок сделок продления: идемпотентная генерация цикла, синхронизация
авто-стадии по посещаемости и балансу, переоткрытие закрытых сделок.

Движок НИКОГДА не закрывает сделку как «Продлён» сам — окончательное решение
о продлении принимает менеджер вручную (repository.move_deal). Все операции
здесь безопасны к повторному вызову. Стадии берём из дефолтной воронки;
авто-стадия прогресса — kind='progress'.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.core.utils.dates import msk_now
from apps.renewals import cycle
from apps.renewals.models import RenewalActivity, RenewalDeal, RenewalPipeline, RenewalStage
from apps.renewals.transitions import InvalidTransition

logger = logging.getLogger(__name__)


def _default_pipeline() -> RenewalPipeline:
    return RenewalPipeline.objects.get(is_default=True)


def _stage(pipeline: RenewalPipeline, *, kind: str) -> RenewalStage:
    """Первая по порядку стадия заданного вида (progress/won/lost)."""
    return (RenewalStage.objects
            .filter(pipeline=pipeline, kind=kind)
            .order_by('sort_order').first())


def _progress_stages(pipeline: RenewalPipeline) -> list[RenewalStage]:
    """
    Авто-стадии прогресса «Урок N» воронки, по порядку (sort_order).

    Специально фильтруем is_auto=True, а не просто kind='progress' — superadmin
    может через настройку стадий завести свою decision-подобную стадию с
    kind='progress' (это разрешено сериализатором), и она не должна путать
    нумерацию «Урок 1..N», завязанную только на движковые авто-стадии.
    """
    return list(RenewalStage.objects
                .filter(pipeline=pipeline, kind='progress', is_auto=True)
                .order_by('sort_order'))


def _attended_total(student_id: int) -> float:
    """
    Суммарно посещено уроков за ВСЮ историю ученика (half-lesson 45мин=0.5).
    Делегирует apps.finances.repository.attended_units_total — ЕДИНЫЙ источник
    «отработано», тот же, что и баланс/потребление finances (present=true по всем
    подтипам урока; модель 1c — доп.урок present=true считается как любой урок, а
    исходный пропуск остаётся present=false, поэтому задвоения нет). Локальный
    импорт — чтобы не тянуть finances в граф импорта renewals на старте.
    """
    from apps.finances.repository import attended_units_total
    return float(attended_units_total(student_id))


def cycle_completed(deal: RenewalDeal) -> bool:
    """
    Отработаны ли все 4 урока ТЕКУЩЕГО цикла сделки — по факту посещаемости,
    независимо от того, на какой стадии она сейчас сидит (прогресс-стадия
    или «Ждём оплату» — обе бывают ДО завершения цикла, «Ждём оплату» просто
    когда деньги кончились раньше). Ворота для ручных переходов
    (repository.move_deal): пока цикл не завершён, вручную можно поставить
    только «Ушёл» — решение пользователя 2026-07-17.
    """
    attended = _attended_total(deal.student_id)
    into = attended - (deal.cycle_no - 1) * cycle.LESSONS_PER_CYCLE
    return into >= cycle.LESSONS_PER_CYCLE


@transaction.atomic
def ensure_deal(student_id: int, cycle_no: int) -> RenewalDeal:
    """
    Создать (или вернуть существующую) сделку цикла ученика. Идемпотентно по UNIQUE.

    assignee новой сделки — ТЕКУЩИЙ Student.manager (единый источник правды,
    см. apps.students.services.set_student_manager) — не принимается параметром,
    чтобы не было двух путей его установки.
    """
    from apps.students.models import Student

    pipeline = _default_pipeline()
    progress_stages = _progress_stages(pipeline)
    progress = progress_stages[0] if progress_stages else _stage(pipeline, kind='progress')
    manager_id = (Student.objects.filter(id=student_id)
                  .values_list('manager_id', flat=True).first())
    deal, created = RenewalDeal.objects.get_or_create(
        student_id=student_id, cycle_no=cycle_no,
        defaults={'pipeline': pipeline, 'stage': progress, 'assignee_id': manager_id},
    )
    if created:
        RenewalActivity.objects.create(
            deal=deal, kind='system', to_stage=progress, body='Сделка создана')
    return deal


def next_open_cycle_no(student_id: int, min_cycle_no: int) -> int:
    """
    Первый свободный номер цикла ученика (>= min_cycle_no), не занятый
    закрытой сделкой.

    ensure_deal с занятым номером вернул бы ЗАКРЫТУЮ сделку по
    UNIQUE(student, cycle_no) и не создал бы открытую — поэтому при спавне
    следующего цикла (ручное закрытие «Продлён», ручное создание сделки)
    такие номера нужно перешагивать: например, после переоткрытия/возврата,
    когда более поздний цикл уже закрыт «Ушёл».
    """
    taken = set(RenewalDeal.objects
                .filter(student_id=student_id, cycle_no__gte=min_cycle_no)
                .values_list('cycle_no', flat=True))
    cycle_no = min_cycle_no
    while cycle_no in taken:
        cycle_no += 1
    return cycle_no


def _auto_stages(pipeline: RenewalPipeline) -> dict[str, RenewalStage]:
    """Все авто-стадии воронки по key (Не было урока, Урок 1..3 + awaiting_payment + awaiting_renewal)."""
    return {s.key: s for s in RenewalStage.objects.filter(pipeline=pipeline, is_auto=True)}


def _target_auto_stage(deal: RenewalDeal, attended: float, balance: float,
                       auto: dict, progress_stages: list) -> tuple[Optional[RenewalStage], bool]:
    """
    Целевая авто-стадия сделки и флаг «цикл созрел» (для due_at).

    Прогресс считается от НОМЕРА ЦИКЛА сделки, а не attended % 4 —
    иначе после 4-го урока сделка «заворачивалась» обратно в «Урок 1».
    Приоритет при конфликте (цикл отработан И баланс ≤ 0): «Ждём продление» >
    «Ждём оплату» — более поздняя точка воронки; долг показывается бейджем.
    """
    into = attended - (deal.cycle_no - 1) * cycle.LESSONS_PER_CYCLE
    if into >= cycle.LESSONS_PER_CYCLE and 'awaiting_renewal' in auto:
        return auto['awaiting_renewal'], True
    if balance <= 0 and 'awaiting_payment' in auto:
        return auto['awaiting_payment'], False
    if not progress_stages:
        return None, False
    idx = min(max(int(into), 0), len(progress_stages) - 1)
    return progress_stages[idx], False


@transaction.atomic
def sync_lesson_stage(student_id: int) -> None:
    """
    Держит открытую сделку ученика на правильной авто-стадии по посещаемости
    и балансу (вызывается после записи/правки посещаемости урока и по оплатам).

    Двигает ТОЛЬКО между авто-стадиями (Не было урока, Урок 1–3, Ждём оплату, Ждём продление):
    если менеджер вручную увёл сделку в «Думает»/«Заморожен»/… или она закрыта —
    движок её не трогает.

    «Заморожен» — обычная ручная стадия (спека 2026-07-25), поэтому отсекается
    общим правилом `not deal.stage.is_auto`, без отдельного исключения. Вернуть
    сделку на расчётную авто-стадию может только явный return_from_freeze.
    """
    from apps.finances.repository import balance_for_student

    deal = _open_deal_for_update(student_id)
    if deal is None or not deal.stage.is_auto:
        return

    auto = _auto_stages(deal.pipeline)
    progress_stages = _progress_stages(deal.pipeline)
    attended = _attended_total(student_id)
    balance = float(balance_for_student(student_id))

    target, matured = _target_auto_stage(deal, attended, balance, auto, progress_stages)
    if target is None:
        return

    update_fields: list[str] = []
    if matured and deal.due_at is None:
        # Цикл созрел — с этого момента живой список направлений может разойтись
        # с тем, что ученик реально отходил в цикле (перевод/уход гасит членство).
        # Снимаем здесь, в автоматической точке: членство ещё активно наверняка.
        from apps.renewals.repository import snapshot_directions
        if snapshot_directions(deal):
            update_fields.append('directions_snapshot')
        # Календарная дата по МСК, не UTC — иначе события в окне 00:00–02:59
        # по Москве (21:00–23:59 UTC предыдущих суток) уезжают на день/месяц
        # назад. Тот же класс проблемы, что решает `AT TIME ZONE 'Europe/Moscow'`
        # в apps/students/repository.py::student_stats.
        deal.due_at = msk_now().date()
        update_fields.append('due_at')
    if target.id != deal.stage_id:
        from_stage = deal.stage
        deal.stage = target
        deal.stage_entered_at = timezone.now()
        update_fields += ['stage', 'stage_entered_at']
        RenewalActivity.objects.create(
            deal=deal, kind='system', from_stage=from_stage, to_stage=target,
            body=f'Автопереход: {target.label}')
    if update_fields:
        deal.save(update_fields=update_fields + ['updated_at'])


def sync_lesson_stage_safe(student_id: int, direction_id: Optional[int] = None) -> None:
    """
    Safe-обёртка над sync_lesson_stage для вызова из apps.lessons/apps.teacher_spa
    через transaction.on_commit — сбой синхронизации авто-стадии никогда не должен
    ронять сохранение урока/посещаемости (основной, учебный поток важнее CRM).

    direction_id сохранён в сигнатуре для совместимости вызовов, но сделка —
    сущность УЧЕНИКА (подписочная модель): направление игнорируется.
    """
    try:
        sync_lesson_stage(student_id)
    except Exception:
        logger.exception(
            'renewals: не удалось синхронизировать авто-стадию (student=%s)', student_id)


def _assert_reopenable(deal: RenewalDeal) -> None:
    """
    Переоткрывать можно только ПОСЛЕДНЮЮ сделку ученика.

    Единственная допустимая «более поздняя» сделка — порождённая при закрытии
    сделка цикла N+1, пока она открыта: reopen_deal её и снимает (или помечает,
    если её уже трогали руками). Всё остальное — закрытый более поздний цикл или
    открытая сделка с чужим номером — значит, что ученик прожил цикл дальше.

    Зачем запрет. cycle_no — не свободный счётчик, а функция от посещаемости
    (cycle.open_cycle_no). Ожив старую сделку поверх закрытого более позднего
    цикла, менеджер получал «дыру»: при следующем закрытии «Продлён» спавн
    (repository.move_deal) перешагивал занятый номер через next_open_cycle_no и
    создавал сделку ВПЕРЕДИ реальности — прогресс `attended − (cycle_no−1)×4`
    уходил в минус и карточка показывала «Не было урока» ученику с полной
    историей уроков. Регрессия 2026-08-25 (прод), см. tests/test_reopen_guard.py.
    """
    if reopen_blocked(deal.student_id, deal.cycle_no):
        raise InvalidTransition(
            'Переоткрывать можно только последнюю сделку ученика — '
            'у него есть более поздние циклы')


def reopen_blocked(student_id: int, cycle_no: int) -> bool:
    """Есть ли у ученика более поздняя сделка, мешающая переоткрыть цикл cycle_no.

    Отдельная функция, а не выражение внутри _assert_reopenable: тем же правилом
    считается флаг `can_reopen` в repository.deal_computed, по которому дровер
    прячет кнопку «Переоткрыть». Правило обязано быть одно — иначе UI предложит
    действие, на которое бэк ответит 409.
    """
    return (RenewalDeal.objects
            .filter(student_id=student_id, cycle_no__gt=cycle_no)
            .exclude(cycle_no=cycle_no + 1, outcome_at__isnull=True)
            .exists())


@transaction.atomic
def reopen_deal(deal_id: int, author_id: Optional[int] = None,
                note: str = 'Сделка переоткрыта') -> Optional[RenewalDeal]:
    """
    Переоткрыть закрытую сделку: outcome_at → NULL, стадия → вычисленная авто-стадия.

    Порождённая при закрытии сделка следующего цикла удаляется, если открыта и
    не тронута руками (только системная активность и авто-стадия); иначе остаётся
    с системной пометкой. Возвращает None, если сделка не найдена или не закрыта.
    """
    from apps.finances.repository import balance_for_student

    deal = (RenewalDeal.objects.select_for_update().select_related('stage', 'pipeline')
            .filter(id=deal_id, outcome_at__isnull=False).first())
    if deal is None:
        return None
    _assert_reopenable(deal)

    nxt = (RenewalDeal.objects.select_for_update().select_related('stage')
           .filter(student_id=deal.student_id,
                   cycle_no=deal.cycle_no + 1, outcome_at__isnull=True).first())
    if nxt is not None:
        touched = (not nxt.stage.is_auto
                   or nxt.activities.exclude(kind='system').exists())
        if touched:
            RenewalActivity.objects.create(
                deal=nxt, kind='system',
                body=f'Сделка цикла {deal.cycle_no} переоткрыта — проверьте актуальность')
        else:
            nxt.delete()  # activity уйдёт каскадом

    auto = _auto_stages(deal.pipeline)
    progress_stages = _progress_stages(deal.pipeline)
    attended = _attended_total(deal.student_id)
    balance = float(balance_for_student(deal.student_id))
    from_stage = deal.stage
    target, _matured = _target_auto_stage(deal, attended, balance, auto, progress_stages)
    deal.stage = target or from_stage
    deal.outcome_at = None
    deal.stage_entered_at = timezone.now()
    deal.save(update_fields=['stage', 'outcome_at', 'stage_entered_at', 'updated_at'])
    RenewalActivity.objects.create(
        deal=deal, kind='system', from_stage=from_stage, to_stage=deal.stage,
        author_id=author_id, body=note)
    return deal


def _open_deal_for_update(student_id: int) -> Optional[RenewalDeal]:
    """Открытая сделка ученика с блокировкой (самый поздний цикл). None если нет."""
    return (RenewalDeal.objects
            .select_for_update()
            .select_related('stage', 'pipeline')
            .filter(student_id=student_id, outcome_at__isnull=True)
            .order_by('-cycle_no').first())


@transaction.atomic
def return_to_work(deal_id: int, author_id: Optional[int] = None) -> Optional[RenewalDeal]:
    """«Вернуть в работу»: сделка со стадии-паузы → расчётная авто-стадия.

    Единственный выход со стадии-паузы (решение пользователя 2026-07-25 про
    заморозку: автовыхода по факту записанного урока нет — sync_lesson_stage не
    трогает ручные стадии, см. её докстринг). Валидатор переходов обходим
    осознанно, как reopen_deal: встать на авто-стадию руками правила воронки не
    дают (is_allowed запрещает ручной вход на is_auto=True стадию), а это не
    ручной переход, а пересчёт.

    Работает для ЛЮБОЙ стадии с allow_mid_cycle, не только 'frozen': раз войти
    в неё можно было посреди цикла, выйти тем же пересчётом — единственный
    способ вернуть сделку в нормальный ход воронки. Ученик, доучившийся до конца
    курса и перешедший на другой, так добирает остаток абонемента: цикл считается
    по суммарной посещаемости, направление в него не входит.

    Адресуется по deal_id: так её вызывает UI карточки сделки.

    None, если сделки нет, она закрыта, стоит не на стадии-паузе или расчётной
    авто-стадии не нашлось (воронка без прогресс-авто-стадий — практически
    недостижимо, но молча отвечать «успех», оставив сделку на паузе, нельзя:
    вьюха обязана отдать 409, а не 200).
    """
    from apps.finances.repository import balance_for_student

    deal = (RenewalDeal.objects.select_for_update().select_related('stage', 'pipeline')
            .filter(id=deal_id, outcome_at__isnull=True).first())
    if deal is None or not deal.stage.allow_mid_cycle:
        return None

    auto = _auto_stages(deal.pipeline)
    progress_stages = _progress_stages(deal.pipeline)
    attended = _attended_total(deal.student_id)
    balance = float(balance_for_student(deal.student_id))
    target, _matured = _target_auto_stage(deal, attended, balance, auto, progress_stages)
    if target is None:
        # Возвращать `deal` здесь значило бы соврать вызывающему: стадия и месяц
        # НЕ сброшены, а services.unfreeze_deal отдал бы 200 и UI показал успех.
        return None

    from_stage = deal.stage
    deal.stage = target
    deal.stage_entered_at = timezone.now()
    # Срок есть только у заморозки; у прочих стадий-пауз он и так NULL.
    deal.frozen_until_month = None
    deal.save(update_fields=['stage', 'stage_entered_at', 'frozen_until_month', 'updated_at'])
    RenewalActivity.objects.create(
        deal=deal, kind='system', from_stage=from_stage, to_stage=target,
        author_id=author_id, body=f'Возврат в работу: {from_stage.label}')
    return deal
