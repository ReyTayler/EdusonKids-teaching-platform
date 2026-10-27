"""
Email-напоминания Eduson (спека этапа 3, раздел D) — только семьям учеников.

Две задачи по расписанию кладут письма в ту же очередь, что и Telegram
(NotificationMessage, channel='email'); отправляет их общий диспетчер.

| задача                     | кому          | когда                                  |
|----------------------------|---------------|----------------------------------------|
| send_parent_day_before     | семья ученика | 10:00 накануне                         |
| send_parent_hour_before    | семья ученика | за 55–65 минут до начала               |

Выключатели: общий is_enabled и отдельный parent_reminders_enabled (по
умолчанию выключен) — письмо уходит, только если включены оба.

Напоминания преподавателям — только Telegram (дайджесты 08:00/21:00,
apps.notifications.digests); письма преподавателям убраны по решению владельца
2026-10-27.

Данные собираются пакетно — один-два запроса на школу, без запроса на урок.

Идемпотентность — dedup_key строки очереди: задачи можно запускать сколько
угодно раз, письмо в своё окно уходит один раз.
"""
from __future__ import annotations

import datetime
import logging

from apps.core.utils.dates import MSK, msk_now
from apps.notifications import email_messages, repository, services
from apps.notifications.constants import (
    HOUR_BEFORE_FROM_MINUTES, HOUR_BEFORE_TO_MINUTES,
    KIND_PARENT_DAY_BEFORE, KIND_PARENT_HOUR_BEFORE,
)
from apps.notifications.models import NotificationSettings

logger = logging.getLogger(__name__)

SOURCE_PLANNED = 'planned_lesson'


def _now(now: datetime.datetime | None) -> datetime.datetime:
    return (now or msk_now()).astimezone(MSK)


# --- Родителям: урок завтра / через час -----------------------------------

def _parents_enabled() -> bool:
    """Оба выключателя одним запросом: общий и родительский."""
    row = NotificationSettings.load()
    return row.is_enabled and row.parent_reminders_enabled


def _send_family(rows: list[dict], *, kind: str, build) -> int:
    """
    Письмо каждому адресу семей учеников группы — одно на адрес и занятие.

    Один и тот же адрес может стоять у двух детей в группе (брат и сестра) или
    сразу в двух полях карточки — письмо всё равно одно, с перечнем детей.
    Адрес сравнивается без учёта регистра: почтовые ящики к нему нечувствительны.
    """
    families = repository.family_contacts_by_group({r['group_pk'] for r in rows})
    queued = 0
    for r in rows:
        by_address: dict[str, dict] = {}
        for student in families.get(r['group_pk'], []):
            for email in student['emails']:
                entry = by_address.setdefault(email.lower(), {'to': email, 'children': []})
                if student['name'] not in entry['children']:
                    entry['children'].append(student['name'])
        for key, entry in by_address.items():
            # Сбой сборки стоит одного письма, а не рассылки всей школе; в журнал
            # доставки не пишем (адрес семьи там ни к чему), только в лог.
            try:
                subject, text = build(r, entry['children'])
            except Exception:  # noqa: BLE001
                logger.exception('Не удалось собрать письмо семье, занятие id=%s', r['id'])
                continue
            queued += int(services.enqueue_email(
                kind=kind, to=entry['to'], subject=subject, text=text,
                dedup_key=f'email:{kind}:{r["id"]}:{key}',
                source_kind=SOURCE_PLANNED, source_id=r['id'],
            ))
    return queued


def send_parent_day_before(now: datetime.datetime | None = None) -> int:
    """«Урок завтра» — семьям учеников занятий следующего дня."""
    now = _now(now)
    if not _parents_enabled():
        return 0
    tomorrow = now.date() + datetime.timedelta(days=1)
    return _send_family(
        repository.upcoming_positions_on(tomorrow), kind=KIND_PARENT_DAY_BEFORE,
        build=lambda r, children: email_messages.parent_day_before(
            day=r['scheduled_date'], time=r['scheduled_time'],
            direction=r['direction_name'], children=children),
    )


def send_parent_hour_before(now: datetime.datetime | None = None) -> int:
    """«Урок через час» — занятия, до начала которых (55; 65] минут."""
    now = _now(now)
    if not _parents_enabled():
        return 0
    rows = repository.upcoming_positions_between(
        now + datetime.timedelta(minutes=HOUR_BEFORE_FROM_MINUTES),
        now + datetime.timedelta(minutes=HOUR_BEFORE_TO_MINUTES),
    )
    return _send_family(
        rows, kind=KIND_PARENT_HOUR_BEFORE,
        build=lambda r, children: email_messages.parent_hour_before(
            time=r['scheduled_time'], direction=r['direction_name'], children=children),
    )
