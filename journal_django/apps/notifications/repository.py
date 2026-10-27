"""Запросы раздела «Уведомления»."""
from __future__ import annotations

import datetime

from django.db.models import F, Max, Q, QuerySet

from apps.memberships.models import GroupMembership
from apps.notifications.constants import (
    CHANNEL_CHOICES, KIND_CHOICES, KIND_FILL_DIGEST, KIND_MORNING_DIGEST,
    KIND_PARENT_DAY_BEFORE, KIND_PARENT_HOUR_BEFORE, STATUS_CHOICES,
)
from apps.notifications.models import NotificationMessage
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import PENDING

# Виды сообщений, которые ставит задача по расписанию (вкладка «Расписание»).
SCHEDULED_KINDS = [
    KIND_MORNING_DIGEST, KIND_FILL_DIGEST,
    KIND_PARENT_DAY_BEFORE, KIND_PARENT_HOUR_BEFORE,
]


def filtered(*, kind: str | None, channel: str | None, status: str | None) -> QuerySet:
    """Лента сообщений, новые сверху. Неизвестные значения фильтров игнорируются.

    Сортировка по (-created_at, -id): у auto_now_add секунд/микросекунд может
    не хватить, чтобы различить строки, созданные в одной транзакции — id как
    тай-брейкер даёт детерминированный порядок.
    """
    qs = (NotificationMessage.objects
          .select_related('recipient_teacher')
          .order_by('-created_at', '-id'))
    if kind in KIND_CHOICES:
        qs = qs.filter(kind=kind)
    if channel in CHANNEL_CHOICES:
        qs = qs.filter(channel=channel)
    if status in STATUS_CHOICES:
        qs = qs.filter(status=status)
    return qs


def last_runs() -> dict[str, object]:
    """
    Когда каждая задача по расписанию отработала в последний раз.

    Считаем по данным очереди (max created_at по kind), а не опрашивая
    внутренности Celery-beat: это дёшево и не ломается при перезапуске beat.
    Один запрос на все виды. Ключи — значения kind, плюс 'dispatch'.
    """
    rows = (NotificationMessage.objects
            .filter(kind__in=SCHEDULED_KINDS)
            .values('kind')
            .annotate(last=Max('created_at')))
    by_kind = {row['kind']: row['last'] for row in rows}
    return {
        **{kind: by_kind.get(kind) for kind in SCHEDULED_KINDS},
        'dispatch': NotificationMessage.objects.aggregate(last=Max('sent_at'))['last'],
    }


def counts_by_status() -> dict[str, int]:
    """Сколько сообщений в каждом статусе — для шапки вкладки «Расписание»."""
    return {
        status: NotificationMessage.objects.filter(status=status).count()
        for status in STATUS_CHOICES
    }


# ---------------------------------------------------------------------------
# Email-напоминания семьям (спека этапа 3, раздел D). Все выборки пакетные: один
# запрос на всю школу, группировка в памяти у вызывающего
# (apps.notifications.reminders).
# ---------------------------------------------------------------------------

def _upcoming(window: Q) -> list[dict]:
    """
    Предстоящие занятия активных групп в окне — для родительских напоминаний.

    Только status=pending: у будущего занятия другого «живого» статуса нет
    (overdue — прошедшее, done — урок уже записан), а отменённые и перенесённые
    строки напоминать нельзя — это звать ребёнка на урок, которого не будет.
    Равенство по pending попадает в частичный индекс
    planned_lessons_pending_dt_idx (выборка идёт по всей школе каждые 5 мин).
    """
    return list(
        PlannedLesson.objects
        .filter(window, group__active=True, status=PENDING)
        .order_by('scheduled_date', 'scheduled_time', 'id')
        .values(
            'id', 'seq', 'scheduled_date', 'scheduled_time',
            group_pk=F('group_id'),
            direction_name=F('group__direction__name'),
        )
    )


def upcoming_positions_on(day: datetime.date) -> list[dict]:
    """Занятия на дату (напоминание «урок завтра»)."""
    return _upcoming(Q(scheduled_date=day))


def upcoming_positions_between(start: datetime.datetime,
                               end: datetime.datetime) -> list[dict]:
    """
    Занятия, начинающиеся в (start; end] по МСК (напоминание «через час»).

    Дата и время лежат раздельными колонками, поэтому окно раскладывается на
    условия по дням: окно через полночь — два дня, иначе один.
    """
    window = Q(pk__in=[])
    day = start.date()
    while day <= end.date():
        cond = Q(scheduled_date=day)
        if day == start.date():
            cond &= Q(scheduled_time__gt=start.time())
        if day == end.date():
            cond &= Q(scheduled_time__lte=end.time())
        window |= cond
        day += datetime.timedelta(days=1)
    return _upcoming(window)


def family_contacts_by_group(group_ids) -> dict[int, list[dict]]:
    """
    group_id → активные ученики группы с адресами: ученик, родитель 1, родитель 2.

    Один запрос на любое число групп. Пустые адреса отбрасываются здесь, чтобы
    вызывающему не пришлось помнить, что '' — тоже «адреса нет».
    """
    ids = list({i for i in group_ids if i is not None})
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    rows = (GroupMembership.objects
            .filter(group_id__in=ids, active=True)
            .order_by('group_id', 'student_id')
            .values('group_id', 'student_id',
                    name=F('student__full_name'),
                    email=F('student__email'),
                    parent1_email=F('student__parent1_email'),
                    parent2_email=F('student__parent2_email')))
    for r in rows:
        emails = [e.strip() for e in (r['email'], r['parent1_email'], r['parent2_email'])
                  if e and e.strip()]
        out.setdefault(r['group_id'], []).append({
            'student_id': r['student_id'], 'name': r['name'], 'emails': emails,
        })
    return out
