"""
Сборка и рассылка дайджестов.

Оба дайджеста — только в личку (спека, п. 2.5). Данные собираются ОДНИМ запросом
на всю школу и группируются в памяти: сто преподавателей — это два запроса,
а не двести. VPS 2 CPU / 2 ГБ, запрос на преподавателя здесь недопустим.
"""
from __future__ import annotations

import datetime
import logging

from django.db.models import F

from apps.core.utils.dates import msk_now
from apps.dashboard import fill_service
from apps.extra_lessons import repository as extra_repo
from apps.groups.models import Group
from apps.notifications import messages, services
from apps.notifications.constants import CHANNEL_DM, KIND_FILL_DIGEST, KIND_MORNING_DIGEST
from apps.notifications.models import TelegramRecipient
from apps.scheduling import repository as scheduling_repo
from apps.scheduling.occurrences import CANCELLED, MOVED

logger = logging.getLogger(__name__)


def _payload(*, teacher_id: int, teacher_name: str, chat_id: int, build) -> dict:
    """
    Письмо одному преподавателю; сбой сборки не выпускается наружу.

    Дайджест собирает сообщения всей школы одним проходом — так на сотне
    преподавателей выходит два запроса, а не двести. Обратная сторона: пока
    исключение поднималось наружу, оно рвало проход целиком и без письма
    оставались ВСЕ (прод, 06.08.2026). Ошибка на одном письме должна стоить
    одного письма.

    Ловим Exception целиком: заранее известны только те сбои, что уже случились,
    а тишина по всей школе — самое дорогое, чем можно расплатиться за аккуратный
    список типов. Трассировка уходит в лог, причина — в журнал уведомлений
    (см. services.record_failure), поэтому проглатыванием это не становится.

    Возвращает payload с готовым text либо с error — разбирает вызывающий.
    """
    card = {'teacher_id': teacher_id, 'teacher_name': teacher_name, 'chat_id': chat_id}
    try:
        return {**card, 'text': build(), 'error': None}
    except Exception as exc:  # noqa: BLE001 — см. докстроку
        logger.exception('Не удалось собрать дайджест преподавателю id=%s', teacher_id)
        return {**card, 'text': None, 'error': f'{type(exc).__name__}: {exc}'}


def _active_recipients() -> dict[int, tuple[int, str]]:
    """teacher_id → (chat_id, ФИО). Только активные привязки."""
    rows = (TelegramRecipient.objects
            .filter(is_active=True)
            .values_list('teacher_id', 'telegram_user__chat_id', 'teacher__name'))
    return {teacher_id: (chat_id, name) for teacher_id, chat_id, name in rows}


def _collect_day(day: datetime.date) -> dict[int, list[dict]]:
    """
    Занятия дня по всей школе, сгруппированные по эффективному преподавателю.

    Плановые занятия: planned_lessons_in_window уже фильтрует активные группы и
    учитывает замену — занятие попадает в день заменяющего, а не основного.
    Отменённые и перенесённые строки исключаются.
    """
    result: dict[int, list[dict]] = {}

    for row in scheduling_repo.planned_lessons_in_window(day, day, teacher_id=None):
        if row['status'] in (CANCELLED, MOVED):
            continue
        substitute_id = row['substitute_teacher_id']
        effective = substitute_id or row['teacher_id']
        if effective is None:
            continue
        result.setdefault(effective, []).append({
            'time': row['scheduled_time'].strftime('%H:%M') if row['scheduled_time'] else '—',
            'group': row['group_name'],
            'direction': row['direction_name'],
            'seq': row['seq'],
            'is_substitute': substitute_id is not None,
            'is_extra': False,
        })

    extra_rows = extra_repo.scheduled_on(day)
    if extra_rows:
        group_ids = {r['group_pk'] for r in extra_rows if r['group_pk']}
        groups = {
            g['id']: g for g in Group.objects
            .filter(id__in=group_ids)
            .values('id', 'name', direction_name=F('direction__name'))
        }
        for row in extra_rows:
            teacher_id = row['assigned_teacher_id']
            if teacher_id is None:
                continue
            group = groups.get(row['group_pk']) or {}
            result.setdefault(teacher_id, []).append({
                'time': row['scheduled_time'].strftime('%H:%M') if row['scheduled_time'] else '—',
                'group': group.get('name', '—'),
                'direction': group.get('direction_name', '—'),
                'seq': None,
                'is_substitute': False,
                'is_extra': True,
            })

    for items in result.values():
        items.sort(key=lambda i: i['time'])
    return result


def send_morning_digest(day: datetime.date | None = None, *,
                        only_teacher_id: int | None = None,
                        dedup_suffix: str = '') -> int:
    """
    Разослать утренний список занятий. Возвращает число поставленных сообщений.

    only_teacher_id и dedup_suffix нужны только ручной проверке
    (manage.py send_digest): первый ограничивает рассылку одним человеком,
    второй позволяет отправить повторно в тот же день, обойдя идемпотентность.
    Beat вызывает функцию без них.
    """
    day = day or msk_now().date()

    if not services.notifications_enabled():
        logger.info('Рассылка выключена — дайджест не отправляется.')
        return 0

    queued = 0
    for payload in morning_payloads(day, only_teacher_id=only_teacher_id):
        key = f'morning:{payload["teacher_id"]}:{day.isoformat()}{dedup_suffix}'
        if _recorded_failure(payload, kind=KIND_MORNING_DIGEST, dedup_key=key):
            continue
        created = services.enqueue(
            kind=KIND_MORNING_DIGEST, channel=CHANNEL_DM,
            chat_id=payload['chat_id'], text=payload['text'],
            dedup_key=key,
            recipient_teacher_id=payload['teacher_id'],
        )
        queued += int(created)
    return queued


def _recorded_failure(payload: dict, *, kind: str, dedup_key: str) -> bool:
    """
    Письмо не собралось — положить строку в журнал уведомлений. True, если так и было.

    Ключ идемпотентности — свой, с суффиксом ':error'. Две вещи разом: повторный
    прогон в тот же день не плодит записей об одном сбое, а рабочий ключ остаётся
    свободным, поэтому после починки письмо в тот же день всё-таки уйдёт.
    """
    if not payload['error']:
        return False
    services.record_failure(
        kind=kind, channel=CHANNEL_DM, chat_id=payload['chat_id'],
        dedup_key=f'{dedup_key}:error',
        recipient_teacher_id=payload['teacher_id'],
        error=payload['error'],
    )
    return True


def morning_payloads(day: datetime.date,
                     only_teacher_id: int | None = None) -> list[dict]:
    """
    Готовые сообщения утреннего дайджеста, ещё не поставленные в очередь.

    Отделено от рассылки, чтобы можно было посмотреть текст глазами, ничего не
    отправляя (manage.py send_digest --dry-run). Возвращает
    [{'teacher_id', 'teacher_name', 'chat_id', 'text', 'error'}]; при сбое сборки
    text=None, error заполнен (см. _payload).
    """
    recipients = _active_recipients()
    out: list[dict] = []
    for teacher_id, items in _collect_day(day).items():
        if not items:
            continue
        if only_teacher_id is not None and teacher_id != only_teacher_id:
            continue
        target = recipients.get(teacher_id)
        if target is None:
            continue
        chat_id, teacher_name = target
        out.append(_payload(
            teacher_id=teacher_id, teacher_name=teacher_name, chat_id=chat_id,
            build=lambda name=teacher_name, rows=items: messages.morning_digest(
                teacher_name=name, day=day, items=rows),
        ))
    return out


def send_fill_digest(day: datetime.date | None = None, *,
                     only_teacher_id: int | None = None,
                     dedup_suffix: str = '') -> int:
    """
    Разослать список незаполненных отчётов.

    only_teacher_id и dedup_suffix — только для ручной проверки
    (manage.py send_digest), beat вызывает функцию без них.

    Логика «что считается незаполненным» НЕ переписывается: берём тот же расчёт,
    что питает вкладку «Заполнить» дашборда, иначе через полгода два места разъедутся.

    Номер урока — ПОРЯДКОВЫЙ (seq), не `lesson_number` (внутренний вес для денег,
    на 45-минутных курсах lesson_number = seq * 0.5). unfilled_lessons() теперь
    отдаёт готовый `seq` для плановых строк (см. apps.dashboard.fill_service и
    apps.scheduling.repository.unfilled_planned_lessons) — округление lesson_number
    сюда больше не годится и не используется.
    """
    day = day or msk_now().date()

    if not services.notifications_enabled():
        logger.info('Рассылка выключена — дайджест не отправляется.')
        return 0

    queued = 0
    for payload in fill_payloads(day, only_teacher_id=only_teacher_id):
        key = f'fill:{payload["teacher_id"]}:{day.isoformat()}{dedup_suffix}'
        if _recorded_failure(payload, kind=KIND_FILL_DIGEST, dedup_key=key):
            continue
        created = services.enqueue(
            kind=KIND_FILL_DIGEST, channel=CHANNEL_DM,
            chat_id=payload['chat_id'], text=payload['text'],
            dedup_key=key,
            recipient_teacher_id=payload['teacher_id'],
        )
        queued += int(created)
    return queued


def fill_payloads(day: datetime.date,
                  only_teacher_id: int | None = None) -> list[dict]:
    """
    Готовые сообщения вечернего дайджеста, ещё не поставленные в очередь.

    Отделено от рассылки ради предпросмотра (manage.py send_digest --dry-run).
    Возвращает [{'teacher_id', 'teacher_name', 'chat_id', 'text', 'error'}]; при
    сбое сборки text=None, error заполнен (см. _payload).

    Строки раскладываются по преподавателям как есть, а разбор значений (дата,
    номер занятия, название направления) происходит уже внутри защищённой сборки
    письма: кривая строка тогда стоит одного письма, а не всей рассылки.
    """
    recipients = _active_recipients()
    by_teacher: dict[int, list[dict]] = {}
    for row in fill_service.unfilled_lessons(sort_dir='asc'):
        teacher_id = row.get('teacher_id')
        if teacher_id is None:
            continue
        if only_teacher_id is not None and teacher_id != only_teacher_id:
            continue
        by_teacher.setdefault(teacher_id, []).append(row)

    out: list[dict] = []
    for teacher_id, rows in by_teacher.items():
        target = recipients.get(teacher_id)
        if target is None:
            continue
        chat_id, teacher_name = target
        out.append(_payload(
            teacher_id=teacher_id, teacher_name=teacher_name, chat_id=chat_id,
            build=lambda src=rows: messages.fill_digest(
                items=[_fill_item(r) for r in src]),
        ))
    return out


def _fill_item(row: dict) -> dict:
    """Строка вкладки «Заполнить» — в вид, понятный тексту сообщения."""
    return {
        'date': datetime.date.fromisoformat(row['date']),
        'time': row['time'] or '—',
        'group': row['group_name'],
        'direction': row['direction_name'],
        'seq': row.get('seq'),
    }
