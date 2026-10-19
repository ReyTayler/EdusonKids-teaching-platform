"""Чистая логика инференса года для конвертации frozen_until_month → дата.

Отдельный модуль (а не тело миграции), чтобы покрыть логику unit-тестом:
«заморозка до месяца M» — ближайшее наступление 1-го числа месяца M, не раньше
текущего месяца (M >= текущий → этот год; M < текущий → следующий год)."""
from __future__ import annotations

import datetime


def infer_frozen_until(month: int, today: datetime.date) -> datetime.date:
    year = today.year if month >= today.month else today.year + 1
    return datetime.date(year, month, 1)


def clamp_frozen_from(today: datetime.date, frozen_until: datetime.date) -> datetime.date:
    """frozen_from никогда не должен превышать frozen_until (иначе нарушится
    CHECK frozen_from <= frozen_until, добавляемый позже в этом же плане).
    Best-effort: реальная дата начала паузы в старой модели не хранилась."""
    return min(today, frozen_until)
