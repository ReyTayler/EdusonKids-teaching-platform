"""
Пересчёт начислений поверх БД (спека 2026-10-20-eduson-motivation-payroll-design, §2–§3).

Правила — в apps/payroll/engine.py; здесь только сбор входов и запись результата:
версии, персональные ставки, уроки (присутствие одним агрегатом) и маркеры отмен
грузятся фиксированным числом запросов, запись — bulk_create/bulk_update только
реально изменившихся строк. Число запросов не зависит от числа уроков.

Порядок блокировок внутри пересчёта — «урок/план → деньги» (как у
scheduling.repository.cancel_lesson: сначала строки плана, затем зависимые):
уроки охвата по id, затем маркеры по id, затем Payroll по lesson_id и
CancellationPay по id. Два параллельных пересчёта поэтому не встают в дедлок
друг с другом; лок урока до чтения агрегата присутствия защищает от расчёта по
устаревшей посещаемости и от двойного bulk_create одного начисления. Внешние
операции (удаление маркеров reset_plan/wipe_one_offs, удаление урока
delete_lesson_full) к тому же порядку приводятся отдельно (задача 3) — пока
этого нет, дедлок с ними не исключён.
"""
from __future__ import annotations

import datetime

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import Coalesce

from apps.core.utils.dates import msk_now
from apps.payroll.engine import (
    LessonInput,
    MarkerInput,
    PersonalRateInput,
    RateCard,
    cancellation_pay,
    lesson_pay,
    rates_on,
)

__all__ = ['load_rate_cards', 'recalculate']

_CANCELLED = 'cancelled'
_BATCH = 500

_PAYROLL_FIELDS = ['teacher_id', 'total_students', 'present_count', 'payment', 'penalty',
                   'calc_payment', 'calc_penalty', 'rule_note']
_CANCEL_FIELDS = ['planned_lesson_id', 'teacher_id', 'group_id', 'lesson_date',
                  'scheduled_time', 'payment', 'calc_payment', 'rule_note']


def load_rate_cards() -> list[RateCard]:
    """Все версии ставок со ставками направлений — два запроса на весь пересчёт."""
    from apps.payroll.models import PayRateVersion
    return [RateCard.from_version(v)
            for v in PayRateVersion.objects.prefetch_related('directions')]


def _load_personal(group_ids: set[int]) -> dict[int, list[PersonalRateInput]]:
    """Персональные ставки групп пересчёта одним запросом, по группам."""
    from apps.payroll.models import PersonalRate
    by_group: dict[int, list[PersonalRateInput]] = {}
    if not group_ids:
        return by_group
    for row in PersonalRate.objects.filter(group_id__in=group_ids).values(
            'teacher_id', 'group_id', 'lesson_from', 'lesson_to', 'kind', 'value'):
        by_group.setdefault(row['group_id'], []).append(PersonalRateInput(**row))
    return by_group


def _markers():
    """Маркеры отмен, за которые есть кому платить: эффективный преподаватель —
    замена, если поставлена (её ставят и на уже отменённое занятие), иначе teacher.
    Маркер без преподавателя пропускается: teacher у начисления NOT NULL."""
    from apps.scheduling.models import PlannedLesson
    return (
        PlannedLesson.objects.filter(status=_CANCELLED, seq__isnull=True)
        .annotate(eff_teacher_id=Coalesce('substitute_teacher_id', 'teacher_id'))
        .filter(eff_teacher_id__isnull=False)
    )


def _scope(qs, *, group_ids, date_field, date_from, date_to):
    if group_ids is not None:
        qs = qs.filter(group_id__in=list(group_ids))
    if date_from is not None:
        qs = qs.filter(**{f'{date_field}__gte': date_from})
    if date_to is not None:
        qs = qs.filter(**{f'{date_field}__lte': date_to})
    return qs


def _diff(obj, values: dict) -> bool:
    """Записывает в obj отличающиеся значения; True — если что-то поменялось."""
    changed = False
    for name, value in values.items():
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            changed = True
    return changed


def _lock_lessons(lesson_qs) -> list[int]:
    # Отдельным запросом: агрегат с GROUP BY под FOR UPDATE не лочится.
    return list(lesson_qs.select_for_update().order_by('id').values_list('id', flat=True))


def _lesson_rows(lesson_ids: list[int]) -> list[dict]:
    """Уроки с группой, источником и присутствием — один запрос с агрегатом.
    Читается ПОСЛЕ лока уроков: посещаемость уже не поменяется под нами."""
    from apps.lessons.models import Lesson
    if not lesson_ids:
        return []
    return list(
        Lesson.objects.filter(id__in=lesson_ids).values(
            'id', 'teacher_id', 'group_id', 'lesson_date', 'lesson_number', 'lesson_type',
            'submitted_at', 'submitted_by_token',
            'group__is_individual', 'group__direction_id', 'group__direction__name',
        ).annotate(
            # Headcount (правило 2026-09-28): unpaid_skip — ученика на уроке как будто
            # нет (вне total и вне present); is_free оплачивается как присутствие.
            total=Count('attendance', filter=Q(attendance__unpaid_skip=False)),
            present=Count('attendance', filter=Q(attendance__unpaid_skip=False,
                                                 attendance__present=True)),
        ).order_by('id')
    )


def _lock_markers(marker_qs) -> list[dict]:
    # of=('self',): лочим только строки плана, не группы/направления из JOIN.
    return list(
        marker_qs.select_for_update(of=('self',)).order_by('id').values(
            'id', 'eff_teacher_id', 'group_id', 'scheduled_date', 'scheduled_time',
            'group__is_individual', 'group__direction_id', 'group__direction__name',
        )
    )


def _recalc_lessons(rows, cards, personal_by_group) -> dict:
    from apps.payroll.models import Payroll

    # Лок существующих строк: ручная правка (PATCH) между чтением и записью
    # иначе была бы тихо затёрта расчётом.
    existing = {
        p.lesson_id: p
        for p in Payroll.objects.select_for_update()
        .filter(lesson_id__in=[r['id'] for r in rows]).order_by('lesson_id')
    }

    to_create, to_update = [], []
    for row in rows:
        lesson = LessonInput(
            teacher_id=row['teacher_id'], group_id=row['group_id'],
            lesson_date=row['lesson_date'], lesson_number=row['lesson_number'],
            is_individual=row['group__is_individual'],
            direction_id=row['group__direction_id'],
            direction_name=row['group__direction__name'],
            submitted_at=row['submitted_at'], submitted_by_token=row['submitted_by_token'],
            present_count=row['present'], lesson_type=row['lesson_type'],
        )
        res = lesson_pay(lesson, rates_on(cards, lesson.lesson_date),
                         personal_by_group.get(lesson.group_id, ()))
        values = {
            'teacher_id': lesson.teacher_id,
            'total_students': row['total'], 'present_count': row['present'],
            'calc_payment': res.payment, 'calc_penalty': res.penalty,
            'rule_note': res.rule_note,
        }
        current = existing.get(row['id'])
        if current is None:
            to_create.append(Payroll(lesson_id=row['id'], payment=res.payment,
                                     penalty=res.penalty, is_manual=False, **values))
            continue
        if not current.is_manual:
            # Ручная правка (§1.9) хранит свои payment/penalty; остальным — расчёт.
            values['payment'] = res.payment
            values['penalty'] = res.penalty
        if _diff(current, values):
            to_update.append(current)

    Payroll.objects.bulk_create(to_create, batch_size=_BATCH)
    Payroll.objects.bulk_update(to_update, _PAYROLL_FIELDS, batch_size=_BATCH)
    return {'created': len(to_create), 'updated': len(to_update)}


def _recalc_cancellations(marker_rows, orphan_scope, cards, personal_by_group,
                          today: datetime.date) -> dict:
    """Начисления маркеров охвата + сироты (маркер удалён) в orphan_scope.

    orphan_scope — queryset CancellationPay без маркера или None (сирот не трогаем)."""
    from apps.payroll.models import CancellationPay

    pays_filter = Q(planned_lesson_id__in=[r['id'] for r in marker_rows])
    if orphan_scope is not None:
        pays_filter |= Q(id__in=orphan_scope.values('id'))
    pays = list(
        CancellationPay.objects.select_for_update(of=('self',)).filter(pays_filter)
        .annotate(
            # Маркер с тем же ключом вне охвата пересчёта: сироту не удаляем — её
            # привяжет к нему пересчёт, который этот маркер затронет. Маркер без
            # преподавателя не в счёт: он не привяжет никогда, сирота жила бы вечно.
            has_marker=Exists(_markers().filter(
                group_id=OuterRef('group_id'), scheduled_date=OuterRef('lesson_date'),
                scheduled_time=OuterRef('scheduled_time'))),
        ).order_by('id')
    )
    linked = {p.planned_lesson_id: p for p in pays if p.planned_lesson_id is not None}
    # Сироты по естественному ключу; при дублях ключа старшая строка первой.
    orphans: dict[tuple, list] = {}
    for p in pays:
        if p.planned_lesson_id is None:
            orphans.setdefault((p.group_id, p.lesson_date, p.scheduled_time), []).append(p)

    to_create, to_update = [], []
    scope_keys = set()
    for row in marker_rows:
        key = (row['group_id'], row['scheduled_date'], row['scheduled_time'])
        scope_keys.add(key)
        marker = MarkerInput(
            teacher_id=row['eff_teacher_id'], group_id=row['group_id'],
            scheduled_date=row['scheduled_date'],
            is_individual=row['group__is_individual'],
            direction_id=row['group__direction_id'],
            direction_name=row['group__direction__name'],
        )
        res = cancellation_pay(marker, rates_on(cards, marker.scheduled_date),
                               personal_by_group.get(marker.group_id, ()))
        values = {
            'planned_lesson_id': row['id'],
            'teacher_id': marker.teacher_id, 'group_id': marker.group_id,
            'lesson_date': marker.scheduled_date, 'scheduled_time': row['scheduled_time'],
            'calc_payment': res.payment, 'rule_note': res.rule_note,
        }
        current = linked.get(row['id'])
        if current is None:
            # Привязка сироты сохраняет её ручную правку (§2): маркер пересоздан,
            # а отмена та же.
            bucket = orphans.get(key)
            current = bucket.pop(0) if bucket else None
        if current is None:
            to_create.append(CancellationPay(payment=res.payment, is_manual=False, **values))
            continue
        if not current.is_manual:
            values['payment'] = res.payment
        if _diff(current, values):
            to_update.append(current)

    # Будущей отмены больше нет — начисление убираем; прошлую сироту не трогаем:
    # выплаченный месяц не должен меняться задним числом. Лишние сироты ключа,
    # чей маркер в охвате (уже привязан к одной из них), — тоже под удаление.
    stale = [
        p.id for key, bucket in orphans.items() for p in bucket
        if p.lesson_date >= today and (key in scope_keys or not p.has_marker)
    ]
    deleted = 0
    if stale:
        deleted, _ = CancellationPay.objects.filter(id__in=stale).delete()

    CancellationPay.objects.bulk_create(to_create, batch_size=_BATCH)
    CancellationPay.objects.bulk_update(to_update, _CANCEL_FIELDS, batch_size=_BATCH)
    return {'created': len(to_create), 'updated': len(to_update), 'deleted': deleted}


def recalculate(*, lesson_ids=None, marker_ids=None, group_ids=None,
                date_from: datetime.date | None = None,
                date_to: datetime.date | None = None) -> dict:
    """
    Пересчитать начисления за уроки и отмены. Фильтры комбинируются через AND;
    без фильтров — вся история.

    Охват:
      * только lesson_ids — пересчитываются эти уроки, начисления маркеров не трогаются;
      * только marker_ids — эти маркеры, уроки не трогаются;
      * group_ids / date_from / date_to сужают оба множества (даты: lesson_date
        урока, scheduled_date маркера, lesson_date начисления-сироты).
    Сироты отмен (маркер удалён) с датой ≥ сегодня (МСК) удаляются в группах
    охвата при любом пересчёте: group_ids, а при lesson_ids/marker_ids — группы
    этих строк; в пределах date_from/date_to. Прошлые сироты не трогаются.

    Возвращает {'lessons', 'cancellations', 'created', 'updated', 'deleted',
    'changed'}: lessons/cancellations — сколько уроков и маркеров рассмотрено;
    created/updated/deleted — строк Payroll + CancellationPay; changed — их сумма.
    """
    from apps.lessons.models import Lesson
    from apps.payroll.models import CancellationPay

    with_lessons = not (lesson_ids is None and marker_ids is not None)
    with_markers = not (marker_ids is None and lesson_ids is not None)
    by_ids = lesson_ids is not None or marker_ids is not None
    # Одно «сегодня» на весь пересчёт: иначе на границе суток выборка сирот и
    # их удаление разошлись бы.
    today = msk_now().date()

    with transaction.atomic():
        # 1. Уроки охвата (лок по id), затем агрегат присутствия.
        lesson_rows: list[dict] = []
        if with_lessons:
            lesson_qs = _scope(Lesson.objects.all(), group_ids=group_ids,
                               date_field='lesson_date', date_from=date_from, date_to=date_to)
            if lesson_ids is not None:
                lesson_qs = lesson_qs.filter(id__in=list(lesson_ids))
            lesson_rows = _lesson_rows(_lock_lessons(lesson_qs))

        # 2. Маркеры охвата (лок по id).
        marker_rows: list[dict] = []
        if with_markers:
            marker_qs = _scope(
                _markers(), group_ids=group_ids, date_field='scheduled_date',
                date_from=date_from, date_to=date_to)
            if marker_ids is not None:
                marker_qs = marker_qs.filter(id__in=list(marker_ids))
            marker_rows = _lock_markers(marker_qs)

        # Сироты — в группах охвата. При точечном пересчёте это группы
        # затронутых строк: удаление маркера (reset_plan) иначе оставило бы
        # будущую сироту до ближайшего полного пересчёта.
        touched = {r['group_id'] for r in lesson_rows} | {r['group_id'] for r in marker_rows}
        orphan_groups = touched if by_ids else group_ids
        orphan_scope = None
        if not by_ids or touched:
            orphan_scope = _scope(CancellationPay.objects.filter(planned_lesson__isnull=True),
                                  group_ids=orphan_groups, date_field='lesson_date',
                                  date_from=date_from, date_to=date_to)
            if not marker_rows:
                # Привязывать не к чему — нужны лишь будущие (кандидаты на удаление).
                orphan_scope = orphan_scope.filter(lesson_date__gte=today)

        cards = load_rate_cards()
        personal_by_group = _load_personal(touched)

        # 3. Деньги: Payroll, затем CancellationPay.
        stats = {'lessons': len(lesson_rows), 'cancellations': len(marker_rows),
                 'created': 0, 'updated': 0, 'deleted': 0}
        if lesson_rows:
            res = _recalc_lessons(lesson_rows, cards, personal_by_group)
            stats['created'] += res['created']
            stats['updated'] += res['updated']
        if marker_rows or orphan_scope is not None:
            res = _recalc_cancellations(marker_rows, orphan_scope, cards, personal_by_group,
                                        today)
            stats['created'] += res['created']
            stats['updated'] += res['updated']
            stats['deleted'] += res['deleted']

    stats['changed'] = stats['created'] + stats['updated'] + stats['deleted']
    return stats
