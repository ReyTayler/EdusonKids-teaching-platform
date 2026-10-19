"""
Тексты email-напоминаний Eduson Kids. Чистые функции: данные на входе,
(тема, текст письма) на выходе.

Письма — простой текст, без HTML: экранировать нечего, и поэтому строки здесь
собраны отдельно от messages.py (там HTML-разметка Telegram, где «&» в названии
группы пришлось бы показывать как «&amp;»).

ПДн — по минимуму (спека этапа 3, раздел D): имя ребёнка — только в письме его
же семье, в письмах преподавателю — группа и номер урока, без учеников.
Номер урока — ПОРЯДКОВЫЙ seq, не вес lesson_number (см. messages.py).
"""
from __future__ import annotations

import datetime

from django.conf import settings

SIGNATURE = 'Eduson Kids'


def _d(day: datetime.date) -> str:
    return day.strftime('%d.%m')


def _t(time: datetime.time | None) -> str:
    return time.strftime('%H:%M') if time else '—'


def _place(group: str | None, direction: str | None) -> str:
    group = group or '—'
    return f'{group} ({direction})' if direction else group


def cabinet_url() -> str | None:
    """Ссылка на кабинет преподавателя или None, если адрес сайта не настроен."""
    base = (getattr(settings, 'PUBLIC_URL', '') or '').rstrip('/')
    return f'{base}/teacher/calendar' if base else None


def _cabinet_line() -> str:
    url = cabinet_url()
    return f'Кабинет преподавателя: {url}' if url else 'Откройте кабинет преподавателя в журнале.'


def _children(names: list[str]) -> str:
    label = 'Ученик' if len(names) == 1 else 'Ученики'
    return f'{label}: {", ".join(names)}.'


def _lesson(direction: str | None) -> str:
    return f'занятие «{direction}»' if direction else 'занятие'


def teacher_schedule(*, teacher_name: str, day: datetime.date,
                     items: list[dict]) -> tuple[str, str]:
    """Расписание на сегодня. items — строки digests._collect_day, непустые."""
    lines = []
    for i in items:
        head = f"• {i['time']} — {_place(i['group'], i.get('direction'))}"
        if i.get('is_extra'):
            lines.append(f'{head} — доп.урок')
            continue
        line = f"{head} — урок №{i['seq']}" if i.get('seq') is not None else head
        if i.get('is_substitute'):
            line += ' (замена)'
        lines.append(line)
    subject = f'Расписание на сегодня, {_d(day)}: занятий — {len(items)}'
    body = (
        f'Здравствуйте, {teacher_name}!\n\n'
        f'Ваши занятия на сегодня ({_d(day)}):\n\n'
        + '\n'.join(lines) + '\n\n'
        f'{_cabinet_line()}\n\n'
        f'{SIGNATURE}'
    )
    return subject, body


def lesson_unrecorded(*, teacher_name: str, group: str | None, direction: str | None,
                      day: datetime.date, time: datetime.time | None,
                      seq: int | None, attempt: int) -> tuple[str, str]:
    """
    «Урок не записан». attempt=0 — первое письмо, 1..N — повторы через сутки.
    """
    number = f', урок №{seq}' if seq is not None else ''
    what = f'{_place(group, direction)}{number}'
    prefix = 'Напоминание: урок' if attempt else 'Урок'
    subject = f'{prefix} не записан — {group or "группа"}, {_d(day)}'
    still = ' до сих пор' if attempt else ''
    body = (
        f'Здравствуйте, {teacher_name}!\n\n'
        f'Занятие {_d(day)} в {_t(time)} — {what} —{still} не записано в журнале.\n'
        'Пожалуйста, отметьте его.\n\n'
        f'{_cabinet_line()}\n\n'
        'Если занятия не было, сообщите менеджеру или администратору.\n\n'
        f'{SIGNATURE}'
    )
    return subject, body


def parent_day_before(*, day: datetime.date, time: datetime.time | None,
                      direction: str | None, children: list[str]) -> tuple[str, str]:
    """«Урок завтра» — семье ученика."""
    subject = f'Напоминание: занятие завтра в {_t(time)}'
    body = (
        'Здравствуйте!\n\n'
        f'Напоминаем: завтра, {_d(day)}, в {_t(time)} по московскому времени — '
        f'{_lesson(direction)}.\n'
        f'{_children(children)}\n\n'
        f'До встречи!\n{SIGNATURE}'
    )
    return subject, body


def parent_hour_before(*, time: datetime.time | None, direction: str | None,
                       children: list[str]) -> tuple[str, str]:
    """«Урок через час» — семье ученика."""
    subject = f'Занятие через час — в {_t(time)}'
    body = (
        'Здравствуйте!\n\n'
        f'Через час, в {_t(time)} по московскому времени, — {_lesson(direction)}.\n'
        f'{_children(children)}\n\n'
        f'До встречи!\n{SIGNATURE}'
    )
    return subject, body
