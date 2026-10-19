"""Unit: _walk пропускает даты из skip_dates, не расходуя на них номер урока.

Опорные даты: 2026-06-01 — понедельник. day_of_week Вс=0 (Пн=1..Сб=6, Вс=0)."""
from __future__ import annotations

import datetime
from decimal import Decimal

from apps.scheduling.occurrences import Slot, _walk

D = datetime.date
T = datetime.time
MON = 1


def _slot(dow, hh):
    return Slot(day_of_week=dow, start_time=T(hh, 0), effective_from=D(2000, 1, 1))


def test_walk_skips_dates_without_consuming_number():
    # Недельный понедельник, курс из 3 уроков, пропускаем 2-й понедельник (06-08).
    occ = _walk(
        D(2026, 6, 1), [_slot(MON, 10)], Decimal('1'), 3, D(2026, 8, 1),
        skip_dates=frozenset({D(2026, 6, 8)}),
    )
    # 06-08 пропущен → уроки встают на 06-01, 06-15, 06-22 (номера 1,2,3 непрерывны).
    assert [o.date for o in occ] == [D(2026, 6, 1), D(2026, 6, 15), D(2026, 6, 22)]
    assert [o.seq for o in occ] == [1, 2, 3]
    assert [o.lesson_number for o in occ] == [Decimal('1'), Decimal('2'), Decimal('3')]


def test_walk_no_skip_dates_is_unchanged():
    occ = _walk(D(2026, 6, 1), [_slot(MON, 10)], Decimal('1'), 2, D(2026, 8, 1))
    assert [o.date for o in occ] == [D(2026, 6, 1), D(2026, 6, 8)]
