"""
Диспетчер очереди: берёт сообщения, отправляет, разбирается с ответами,
подрезает хвост истории.

Блокировка строк — SELECT ... FOR UPDATE SKIP LOCKED: два воркера никогда не
возьмут одну строку и не пришлют человеку дубль. Захват строки и запись исхода
отправки происходят в ОДНОЙ транзакции (см. _claim_and_send) — иначе между
захватом id и повторным SELECT ... FOR UPDATE другой воркер успел бы забрать
ту же строку и отправить дубль (см. self-review в отчёте задачи).
"""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.notifications import telegram
from apps.notifications.constants import (
    MAX_ATTEMPTS, STATUS_FAILED, STATUS_QUEUED, STATUS_SENT, TERMINAL_STATUSES,
)
from apps.notifications.models import (
    NotificationMessage, NotificationSettings, TelegramRecipient,
)

logger = logging.getLogger(__name__)

# Лимит Telegram — порядка 30 сообщений в секунду. 100 сообщений с паузой 40 мс
# уходят примерно за 4 секунды: утренний дайджест на 100 преподавателей
# укладывается в один прогон.
BATCH_SIZE = 100
PAUSE_BETWEEN_SENDS = 0.04


def dispatch() -> int:
    """
    Один прогон: отправить до BATCH_SIZE сообщений. Возвращает число отправленных.

    Строки берутся по одной, каждая в своей короткой транзакции: длинная
    транзакция на весь батч держала бы блокировки все 4 секунды отправки.

    Сообщение с временной ошибкой (outcome='retry') остаётся в статусе queued,
    но в рамках ЭТОГО прогона повторно не берётся — copied_ids исключает его из
    выборки. Иначе цикл тут же подхватил бы ту же строку снова (она ведь опять
    первая по created_at среди queued) и отправил бы её до BATCH_SIZE раз за
    один вызов dispatch(), вместо того чтобы отправлять по одной попытке на
    прогон и растягивать ретраи по времени.
    """
    # Выключатель гасит и уже стоящее в очереди: щёлкнув тумблер, человек
    # ожидает тишины немедленно, а не «кроме того, что успело накопиться».
    # Сообщения не теряются — остаются в очереди до включения.
    if not NotificationSettings.load().is_enabled:
        return 0

    sent = 0
    excluded_ids: set[int] = set()
    for _ in range(BATCH_SIZE):
        claimed = _claim_and_send(excluded_ids)
        if claimed is None:
            # Очередь опустела — дальше отправлять нечего.
            break
        message_id, outcome = claimed
        if outcome == 'sent':
            sent += 1
        elif outcome == 'rate_limited':
            # Telegram просит притормозить — заканчиваем прогон, остальное
            # уедет следующим тиком beat через минуту.
            break
        elif outcome == 'retry':
            excluded_ids.add(message_id)
        time.sleep(PAUSE_BETWEEN_SENDS)
    trim_history()
    return sent


def _claim_and_send(excluded_ids: set[int]) -> tuple[int, str] | None:
    """
    Забрать следующее сообщение из очереди (кроме excluded_ids) и отправить
    его — атомарно.

    Захват строки (SELECT ... FOR UPDATE SKIP LOCKED) и запись исхода отправки
    находятся в одной транзакции: строка остаётся заблокированной до самого
    save(), поэтому второй воркер, идущий параллельно, физически не может
    увидеть эту же строку ни на захвате, ни между захватом и отправкой.

    Возвращает (id сообщения, 'sent'|'rate_limited'|'failed'|'retry'), либо
    None, если в очереди больше нечего брать.
    """
    with transaction.atomic():
        qs = (NotificationMessage.objects
              .select_for_update(skip_locked=True)
              .filter(status=STATUS_QUEUED))
        if excluded_ids:
            qs = qs.exclude(id__in=excluded_ids)
        message = qs.order_by('created_at').first()
        if message is None:
            return None

        result = telegram.send_message(chat_id=message.chat_id, text=message.text)

        if result.ok:
            message.status = STATUS_SENT
            message.sent_at = timezone.now()
            message.attempts += 1
            message.last_error = None
            message.save(update_fields=['status', 'sent_at', 'attempts', 'last_error'])
            return message.id, 'sent'

        message.attempts += 1
        message.last_error = result.error[:2000]

        if result.blocked:
            # Адресат недоступен — повторять бессмысленно. Гасим сообщение и
            # деактивируем привязку, чтобы это было видно админу в карточке.
            message.status = STATUS_FAILED
            message.save(update_fields=['status', 'attempts', 'last_error'])
            _deactivate_recipient(message)
            return message.id, 'failed'

        if result.retry_after:
            # Лимит скорости — сообщение остаётся в очереди как есть, статус
            # не трогаем, попытка всё равно засчитана для мониторинга.
            message.save(update_fields=['attempts', 'last_error'])
            return message.id, 'rate_limited'

        # Временная ошибка (таймаут, 5xx и т.п.): даём ещё попытки, пока не
        # упрёмся в MAX_ATTEMPTS.
        if message.attempts >= MAX_ATTEMPTS:
            message.status = STATUS_FAILED
        message.save(update_fields=['status', 'attempts', 'last_error'])
        return message.id, ('failed' if message.status == STATUS_FAILED else 'retry')


def _deactivate_recipient(message: NotificationMessage) -> None:
    if message.recipient_teacher_id is None:
        # Сообщение в общий чат — привязки преподавателя нет, деактивировать нечего.
        return
    TelegramRecipient.objects.filter(teacher_id=message.recipient_teacher_id).update(
        is_active=False, blocked_reason=message.last_error,
    )
    logger.warning('Привязка Telegram деактивирована: teacher_id=%s, причина=%s',
                   message.recipient_teacher_id, message.last_error)


def trim_history() -> int:
    """
    Оставить не более NOTIFICATIONS_HISTORY_LIMIT завершённых записей.

    Строки в очереди (status=queued) НЕ удаляются никогда — иначе однажды
    подрезка съест неотправленное сообщение.
    """
    limit = getattr(settings, 'NOTIFICATIONS_HISTORY_LIMIT', 200)
    stale_ids = list(
        NotificationMessage.objects
        .filter(status__in=TERMINAL_STATUSES)
        .order_by('-created_at', '-id')
        .values_list('id', flat=True)[limit:]
    )
    if not stale_ids:
        return 0
    deleted, _ = NotificationMessage.objects.filter(id__in=stale_ids).delete()
    return deleted
