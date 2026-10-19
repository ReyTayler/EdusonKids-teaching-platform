"""Тесты утилит дат (apps/core/utils/dates.py) — подписи и перебор месяцев."""
from __future__ import annotations

from apps.core.utils.dates import month_label, next_month


def test_month_label_renders_russian_month_and_year():
    assert month_label('2026-07') == 'Июль 2026'
    assert month_label('2027-01') == 'Январь 2027'


def test_next_month_rolls_over_the_year():
    assert next_month('2026-07') == '2026-08'
    assert next_month('2026-12') == '2027-01'
