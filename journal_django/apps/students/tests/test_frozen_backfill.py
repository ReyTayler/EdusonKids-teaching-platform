"""Проверка логики конвертации frozen_until_month → frozen_until (месяц+инференс года)."""
import datetime

import pytest

from apps.students.migrations import _frozen_backfill_util as util


def test_month_ge_current_stays_this_year():
    # today = 2026-03-10, месяц заморозки 6 (июнь) → 2026-06-01
    today = datetime.date(2026, 3, 10)
    assert util.infer_frozen_until(6, today) == datetime.date(2026, 6, 1)


def test_month_lt_current_rolls_to_next_year():
    # today = 2026-11-10, месяц заморозки 2 (февраль) → 2027-02-01
    today = datetime.date(2026, 11, 10)
    assert util.infer_frozen_until(2, today) == datetime.date(2027, 2, 1)


def test_month_equal_current_is_this_year():
    today = datetime.date(2026, 5, 1)
    assert util.infer_frozen_until(5, today) == datetime.date(2026, 5, 1)


def test_clamp_frozen_from_caps_at_until_when_today_is_later():
    # today после until (баговый кейс, который раньше давал frozen_from > frozen_until)
    today = datetime.date(2026, 5, 20)
    until = datetime.date(2026, 5, 1)
    assert util.clamp_frozen_from(today, until) == until


def test_clamp_frozen_from_keeps_today_when_until_is_later():
    today = datetime.date(2026, 3, 10)
    until = datetime.date(2026, 6, 1)
    assert util.clamp_frozen_from(today, until) == today
