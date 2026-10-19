"""
Email-напоминания Eduson (спека этапа 3, раздел D).

Четыре задачи по расписанию кладут письма в ту же очередь, что и Telegram
(NotificationMessage, channel='email'); отправляет их общий диспетчер.

| задача                     | кому          | когда                                  |
|----------------------------|---------------|----------------------------------------|
| send_teacher_schedule      | преподаватель | 08:00, если сегодня есть занятия       |
| send_unrecorded_reminders  | преподаватель | 3 ч после конца урока + повторы раз в сутки |
| send_parent_day_before     | семья ученика | 10:00 накануне                         |
| send_parent_hour_before    | семья ученика | за 55–65 минут до начала               |

Выключатели: преподавательские письма подчиняются общему is_enabled,
родительские — ещё и отдельному parent_reminders_enabled (по умолчанию выключен).

Данные собираются пакетно — один-два запроса на школу, без запроса на урок.
Состав занятий дня берётся из той же сборки, что у утреннего Telegram-дайджеста
(digests._collect_day), а «не записан» — по тем же предикатам, что вкладка
«Заполнить»: два места, которые разъедутся, хуже одного.

Идемпотентность — dedup_key строки очереди: задачи можно запускать сколько
угодно раз, письмо в своё окно уходит один раз.
"""
from __future__ import annotations

import datetime
import logging

from apps.core.utils.dates import MSK, msk_now
from apps.notifications import digests, email_messages, repository, services
from apps.notifications.constants import (
    CHANNEL_EMAIL, HOUR_BEFORE_FROM_MINUTES, HOUR_BEFORE_TO_MINUTES,
    KIND_LESSON_UNRECORDED, KIND_MORNING_DIGEST, KIND_PARENT_DAY_BEFORE,
    KIND_PARENT_HOUR_BEFORE, UNRECORDED_MAX_REPEATS, UNRECORDED_REPEAT_EVERY_HOURS,
    UNRECORDED_WINDOW_HOURS,
)
from apps.notifications.models import NotificationSettings

logger = logging.getLogger(__name__)

SOURCE_PLANNED = 'planned_lesson'
# Длительность занятия, если у группы она почему-то не задана (в БД есть
# default и CHECK, так что это страховка, а не рабочий путь).
_FALLBACK_DURATION_MIN = 60


def _now(now: datetime.datetime | None) -> datetime.datetime:
    return (now or msk_now()).astimezone(MSK)


def _teacher_letter(*, kind: str, contact: dict, teacher_id: int, dedup_key: str,
                    build, source_id: int | None = None) -> bool:
    """
    Поставить письмо преподавателю; сбой сборки стоит одного письма.

    Тот же приём, что digests._payload: одна кривая строка не должна оставить
    без писем всю школу. Причина уходит в журнал уведомлений строкой failed с
    ключом ':error' — рабочий ключ остаётся свободным, после починки письмо уйдёт.
    """
    try:
        subject, text = build()
    except Exception as exc:  # noqa: BLE001 — см. докстроку
        logger.exception('Не удалось собрать письмо преподавателю id=%s', teacher_id)
        services.record_failure(
            kind=kind, channel=CHANNEL_EMAIL, dedup_key=f'{dedup_key}:error',
            recipient_teacher_id=teacher_id, error=f'{type(exc).__name__}: {exc}',
            recipient_email=contact['email'], subject='—',
        )
        return False
    return services.enqueue_email(
        kind=kind, to=contact['email'], subject=subject, text=text,
        dedup_key=dedup_key, recipient_teacher_id=teacher_id,
        source_kind=SOURCE_PLANNED if source_id is not None else None,
        source_id=source_id,
    )


# --- 1. Расписание на сегодня ------------------------------------------------

def send_teacher_schedule(day: datetime.date | None = None, *,
                          dedup_suffix: str = '') -> int:
    """Письмо «Расписание на сегодня» каждому преподавателю с занятиями."""
    day = day or msk_now().date()
    if not services.notifications_enabled():
        return 0

    day_map = {tid: items for tid, items in digests._collect_day(day).items() if items}
    contacts = repository.teacher_contacts(day_map.keys())
    queued = 0
    for teacher_id, items in day_map.items():
        contact = contacts.get(teacher_id)
        if contact is None:
            continue
        queued += int(_teacher_letter(
            kind=KIND_MORNING_DIGEST, contact=contact, teacher_id=teacher_id,
            dedup_key=f'email:morning:{teacher_id}:{day.isoformat()}{dedup_suffix}',
            build=lambda c=contact, rows=items: email_messages.teacher_schedule(
                teacher_name=c['name'], day=day, items=rows),
        ))
    return queued


# --- 2. Урок не записан ------------------------------------------------------

def unrecorded_attempt(*, end: datetime.datetime, now: datetime.datetime) -> int | None:
    """
    Номер письма для урока, закончившегося в end: 0 — первое, 1..N — повторы.
    None — сейчас не окно ни одного из писем.

    Окна: [end + k·24 ч; end + k·24 ч + 3 ч) для k = 0..N. Окно, а не «прошло
    больше суток», чтобы задача не будила человека письмами о давнем уроке,
    если beat долго стоял, и чтобы повтор был ровно один на сутки.
    """
    elapsed = now - end
    if elapsed < datetime.timedelta(0):
        return None
    step = datetime.timedelta(hours=UNRECORDED_REPEAT_EVERY_HOURS)
    attempt = int(elapsed // step)
    if attempt > UNRECORDED_MAX_REPEATS:
        return None
    if elapsed - attempt * step >= datetime.timedelta(hours=UNRECORDED_WINDOW_HOURS):
        return None
    return attempt


def send_unrecorded_reminders(now: datetime.datetime | None = None) -> int:
    """Письма «Урок не записан» и повторы к ним. Возвращает число поставленных."""
    now = _now(now)
    if not services.notifications_enabled():
        return 0

    # Самое старое занятие, по которому ещё может идти письмо: последний повтор
    # плюс его окно плюс самая длинная длительность урока (90 мин) с запасом.
    lookback = datetime.timedelta(
        hours=UNRECORDED_REPEAT_EVERY_HOURS * UNRECORDED_MAX_REPEATS
        + UNRECORDED_WINDOW_HOURS + 2)
    rows = repository.unrecorded_positions((now - lookback).date(), now.date())

    due: list[tuple[dict, int, int]] = []
    for r in rows:
        start = datetime.datetime.combine(
            r['scheduled_date'], r['scheduled_time'] or datetime.time(0, 0), tzinfo=MSK)
        end = start + datetime.timedelta(minutes=r['duration'] or _FALLBACK_DURATION_MIN)
        attempt = unrecorded_attempt(end=end, now=now)
        teacher_id = r['substitute_teacher_id'] or r['teacher_id']
        if attempt is None or teacher_id is None:
            continue
        due.append((r, attempt, teacher_id))

    contacts = repository.teacher_contacts({t for _, _, t in due})
    queued = 0
    for r, attempt, teacher_id in due:
        contact = contacts.get(teacher_id)
        if contact is None:
            continue
        queued += int(_teacher_letter(
            kind=KIND_LESSON_UNRECORDED, contact=contact, teacher_id=teacher_id,
            dedup_key=f'email:unrecorded:{r["id"]}:{attempt}',
            source_id=r['id'],
            build=lambda c=contact, row=r, k=attempt: email_messages.lesson_unrecorded(
                teacher_name=c['name'], group=row['group_name'],
                direction=row['direction_name'], day=row['scheduled_date'],
                time=row['scheduled_time'], seq=row['seq'], attempt=k),
        ))
    return queued


# --- 3. Родителям: урок завтра / через час -----------------------------------

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
            # Как _teacher_letter: сбой сборки стоит одного письма, а не рассылки
            # всей школе. Адрес в журнал не пишем — достаточно занятия.
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
