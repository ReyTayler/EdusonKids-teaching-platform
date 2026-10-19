"""
Постановка сообщений в очередь.

Вызывается ИЗ ТОЙ ЖЕ ТРАНЗАКЦИИ, что и доменная операция (transactional outbox):
либо доп.урок назначен и сообщение поставлено, либо не произошло ни то, ни другое.
Состояния «назначили, а сказать забыли» не существует.

Отправку инициирует НЕ транзакция: после коммита (transaction.on_commit) дёргается
диспетчер, чтобы точечные уведомления уходили сразу. Прямой .delay() внутри
транзакции недопустим — задача стартует раньше коммита и не находит данных.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from apps.notifications import messages
from apps.notifications.constants import CHANNEL_DM, CHANNEL_GROUP, STATUS_FAILED
from apps.notifications.models import (
    NotificationMessage, NotificationSettings, TelegramRecipient,
)
from apps.teachers.models import Teacher

logger = logging.getLogger(__name__)

# Текст строки журнала, когда письмо не удалось собрать. Поле text у сообщения
# обязательное, а показать в модалке пустоту — значит не сказать ничего.
FAILED_TEXT = (
    'Письмо не удалось собрать — преподаватель уведомление не получил. '
    'Причина указана выше.'
)


def notifications_enabled() -> bool:
    """
    Включена ли рассылка (общешкольный выключатель из раздела «Уведомления»).

    Проверяется НЕ в enqueue, а в трёх точках выше по стеку: notify_teacher,
    обе рассылки дайджестов и диспетчер. Иначе дайджест на сотню
    преподавателей сделал бы сотню лишних запросов к базе вместо одного.
    """
    return NotificationSettings.load().is_enabled


def enqueue(*, kind: str, channel: str, chat_id: int, text: str, dedup_key: str,
            recipient_teacher_id: int | None = None,
            source_kind: str | None = None, source_id: int | None = None) -> bool:
    """
    Поставить одно сообщение в очередь. Возвращает True, если строка создана.

    Повторная постановка того же dedup_key молча ничего не делает: защита от
    двойного клика, повторного запуска задачи и гонки воркеров — на уровне БД,
    а не на уровне «мы аккуратно написали код».

    Реализовано через get_or_create, а не bulk_create(ignore_conflicts=True):
    на PostgreSQL последний не проставляет pk обратно в объект даже при успешной
    вставке, из-за чего возвращаемое значение всегда было бы False. get_or_create
    честно отдаёт (obj, created) и сам оборачивает INSERT в SAVEPOINT, откатывая
    только его при гонке на уникальном ключе — совместимо с внешней транзакцией
    outbox-паттерна.
    """
    _, created = NotificationMessage.objects.get_or_create(
        dedup_key=dedup_key,
        defaults=dict(
            kind=kind, channel=channel, chat_id=chat_id, text=text,
            recipient_teacher_id=recipient_teacher_id,
            source_kind=source_kind, source_id=source_id,
        ),
    )
    return created


def record_failure(*, kind: str, channel: str, chat_id: int, dedup_key: str,
                   recipient_teacher_id: int | None, error: str) -> bool:
    """
    Записать в журнал письмо, которое не удалось СОБРАТЬ. Возвращает True,
    если строка создана.

    Отказы доставки пишет диспетчер — там сообщение существует, и ему есть куда
    проставить статус. Здесь случай другой: текста нет вовсе, отправлять нечего,
    и без такой записи провал ничем себя не проявляет — сообщений просто нет,
    и это неотличимо от «сегодня рассылать было нечего».

    Статус сразу failed, поэтому диспетчер строку не подберёт: это запись в
    журнале, а не сообщение в очереди.
    """
    _, created = NotificationMessage.objects.get_or_create(
        dedup_key=dedup_key,
        defaults=dict(
            kind=kind, channel=channel, chat_id=chat_id,
            text=FAILED_TEXT, status=STATUS_FAILED,
            last_error=error[:2000],
            recipient_teacher_id=recipient_teacher_id,
        ),
    )
    return created


def active_chat_id(teacher_id: int) -> int | None:
    """chat_id активной привязки преподавателя либо None."""
    return (TelegramRecipient.objects
            .filter(teacher_id=teacher_id, is_active=True)
            .values_list('telegram_user__chat_id', flat=True)
            .first())


def _recipient_card(teacher_id: int) -> dict:
    """
    Всё, что нужно знать о получателе, одним запросом: куда слать и как упомянуть.

    Возвращает {'chat_id', 'username', 'name'}; chat_id/username = None, если
    привязки нет или она деактивирована. name берём из журнала — он есть всегда,
    в отличие от телеграм-ника.
    """
    row = (TelegramRecipient.objects
           .filter(teacher_id=teacher_id, is_active=True)
           .values_list('telegram_user__chat_id', 'telegram_user__username')
           .first())
    name = (Teacher.objects
            .filter(id=teacher_id)
            .values_list('name', flat=True)
            .first()) or 'преподаватель'
    if row is None:
        return {'chat_id': None, 'username': None, 'name': name}
    return {'chat_id': row[0], 'username': row[1], 'name': name}


def notify_teacher(*, kind: str, teacher_id: int, text: str, dedup_prefix: str,
                    source_kind: str, source_id: int,
                    also_to_group_chat: bool) -> None:
    """
    Уведомить преподавателя: личка и, если нужно, дубль в общий чат.

    Дубль — просто вторая строка очереди с другим chat_id, никакой особой логики.
    Нет привязки → личка не создаётся, но общий чат сообщение получает: молчать
    нельзя, иначе изменение (например, назначенный доп.урок) потеряется.
    """
    # Выключатель: пока рассылка выключена, сообщения не создаются вовсе —
    # чтобы при включении не хлынула пачка устаревших.
    if not notifications_enabled():
        return

    card = _recipient_card(teacher_id)
    if card['chat_id'] is not None:
        enqueue(kind=kind, channel=CHANNEL_DM, chat_id=card['chat_id'], text=text,
                dedup_key=f'{dedup_prefix}:dm', recipient_teacher_id=teacher_id,
                source_kind=source_kind, source_id=source_id)

    general = getattr(settings, 'TELEGRAM_GENERAL_CHAT_ID', 0)
    if also_to_group_chat and general:
        # В общем чате сообщение начинается с упоминания адресата: иначе в потоке
        # сообщений человек не поймёт, что это про него, и уведомления чат не даст.
        # В личке упоминание не нужно — там и так понятно, кому пишут.
        mention = messages.mention(
            name=card['name'], username=card['username'], chat_id=card['chat_id'])
        enqueue(kind=kind, channel=CHANNEL_GROUP, chat_id=general,
                text=f'{mention}\n\n{text}',
                dedup_key=f'{dedup_prefix}:group', recipient_teacher_id=teacher_id,
                source_kind=source_kind, source_id=source_id)

    _request_dispatch()


def _request_dispatch() -> None:
    """
    После коммита попросить воркер разгрести очередь — чтобы точечные уведомления
    уходили сразу, а не ждали минутного тика beat.

    Celery локально не установлен (штатный режим, см. config/celery.py): тогда
    импорт задачи падает ещё до .delay(), и это тихо игнорируется — сообщение
    просто остаётся в очереди. Ничего не падает и ничего не теряется.
    """
    def _kick() -> None:
        try:
            from apps.notifications.tasks import dispatch_outbox
            dispatch_outbox.delay()
        except Exception:  # noqa: BLE001 — Celery/Redis недоступны: это не ошибка
            logger.debug('dispatch_outbox не запущен: Celery недоступен')

    transaction.on_commit(_kick)
