"""
Тексты email-напоминаний Eduson Kids — письма семьям учеников. Чистые функции:
данные на входе, (тема, текст письма) на выходе. Преподавателям писем нет (с
2026-10-27 им — только Telegram-дайджесты, apps.notifications.digests).

Письма — простой текст, без HTML: экранировать нечего, и поэтому строки здесь
собраны отдельно от messages.py (там HTML-разметка Telegram, где «&» в названии
группы пришлось бы показывать как «&amp;»).

ПДн — по минимуму (спека этапа 3, раздел D): имя ребёнка — только в письме его
же семье.
"""
from __future__ import annotations

import datetime

SIGNATURE = 'Eduson Kids'


def _d(day: datetime.date) -> str:
    return day.strftime('%d.%m')


def _t(time: datetime.time | None) -> str:
    return time.strftime('%H:%M') if time else '—'


def _children(names: list[str]) -> str:
    label = 'Ученик' if len(names) == 1 else 'Ученики'
    return f'{label}: {", ".join(names)}.'


def _lesson(direction: str | None) -> str:
    return f'занятие «{direction}»' if direction else 'занятие'


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
