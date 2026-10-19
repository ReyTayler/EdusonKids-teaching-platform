"""
Moscow-timezone date utilities for journal_django.

All functions return strings ('YYYY-MM-DD') to be consistent with the
DATE type-parser convention (no Python date objects leave this module).
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo('Europe/Moscow')


def msk_now() -> datetime.datetime:
    """Return the current datetime in Moscow timezone."""
    return datetime.datetime.now(tz=MSK)


def msk_today() -> str:
    """Return today's date in Moscow timezone as 'YYYY-MM-DD'."""
    return msk_now().strftime('%Y-%m-%d')


def msk_month_range_triple(now: datetime.datetime | None = None) -> tuple[str, str, str]:
    """
    Точный порт Node services/calculator.js mskMonthRange().

    Возвращает (month, month_start, month_end), где:
      month       = 'YYYY-MM' текущего МСК-месяца,
      month_start = 'YYYY-MM-01',
      month_end   = ЭКСКЛЮЗИВНОЕ первое число следующего месяца (Dec→Jan переносит год).

    Отличается от msk_month_range() ниже, которая отдаёт ВКЛЮЧИТЕЛЬНЫЙ последний день.
    Дашборд использует именно полуинтервал [month_start, month_end), как в Node.
    """
    today = (now.astimezone(MSK) if now is not None else msk_now()).strftime('%Y-%m-%d')
    y, m = int(today[:4]), int(today[5:7])
    month = f'{y}-{m:02d}'
    month_start = f'{month}-01'
    ny = y + 1 if m == 12 else y
    nm = 1 if m == 12 else m + 1
    month_end = f'{ny}-{nm:02d}-01'
    return month, month_start, month_end


def msk_month_range(d: str | datetime.date | datetime.datetime) -> tuple[str, str]:
    """
    Return the first and last day of the month containing *d* as
    'YYYY-MM-DD' strings.

    Mirrors the Node.js mskMonthRange() in services/calculator.js.

    Args:
        d: 'YYYY-MM-DD' string, date, or datetime object.

    Returns:
        (month_start, month_end) both as 'YYYY-MM-DD' strings.
    """
    if isinstance(d, str):
        date_obj = datetime.date.fromisoformat(d[:10])
    elif isinstance(d, datetime.datetime):
        date_obj = d.astimezone(MSK).date()
    else:
        date_obj = d

    first_day = date_obj.replace(day=1)

    # Last day: first day of next month minus one day
    if first_day.month == 12:
        next_month_first = first_day.replace(year=first_day.year + 1, month=1)
    else:
        next_month_first = first_day.replace(month=first_day.month + 1)

    last_day = next_month_first - datetime.timedelta(days=1)

    return first_day.strftime('%Y-%m-%d'), last_day.strftime('%Y-%m-%d')


_MONTH_NAMES = (
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)


def month_label(ym: str) -> str:
    """'2026-07' → 'Июль 2026'. Подпись месяца-колонки в отчётах."""
    year, month = int(ym[:4]), int(ym[5:7])
    return f'{_MONTH_NAMES[month - 1]} {year}'


def next_month(ym: str) -> str:
    """'2026-12' → '2027-01'."""
    year, month = int(ym[:4]), int(ym[5:7])
    return f'{year + 1}-01' if month == 12 else f'{year}-{month + 1:02d}'
