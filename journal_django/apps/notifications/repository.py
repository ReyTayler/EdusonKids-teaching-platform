"""Запросы раздела «Уведомления»."""
from __future__ import annotations

from django.db.models import Max, QuerySet

from apps.notifications.constants import (
    CHANNEL_CHOICES, KIND_CHOICES, KIND_FILL_DIGEST, KIND_MORNING_DIGEST,
    STATUS_CHOICES,
)
from apps.notifications.models import NotificationMessage


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
    Когда каждый дайджест отработал в последний раз.

    Считаем по данным очереди (max created_at по kind), а не опрашивая
    внутренности Celery-beat: это дёшево и не ломается при перезапуске beat.
    """
    rows = (NotificationMessage.objects
            .filter(kind__in=[KIND_MORNING_DIGEST, KIND_FILL_DIGEST])
            .values('kind')
            .annotate(last=Max('created_at')))
    by_kind = {row['kind']: row['last'] for row in rows}
    return {
        'morning_digest': by_kind.get(KIND_MORNING_DIGEST),
        'fill_digest': by_kind.get(KIND_FILL_DIGEST),
        'dispatch': NotificationMessage.objects.aggregate(last=Max('sent_at'))['last'],
    }


def counts_by_status() -> dict[str, int]:
    """Сколько сообщений в каждом статусе — для шапки вкладки «Расписание»."""
    return {
        status: NotificationMessage.objects.filter(status=status).count()
        for status in STATUS_CHOICES
    }
