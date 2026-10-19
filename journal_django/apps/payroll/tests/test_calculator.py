"""
test_calculator.py — юнит-тесты для apps/payroll/calculator.py.

Чисто логические тесты, без БД.
Матрица: half/small/partial/perStudent, present=0, various combos.
Штраф: тот же день → 0, другой → 40 на присутствовавшего.
"""
from __future__ import annotations

import pytest

from apps.payroll.calculator import calculate_payment, calculate_penalty, calculate_extra_lesson_payment


# ---------------------------------------------------------------------------
# calculate_payment — матрица
# ---------------------------------------------------------------------------

class TestCalculatePayment:
    """Порт матрицы из calculator.js calculatePayment."""

    # present == 0 → всегда 0
    @pytest.mark.parametrize('total,is_half', [
        (1, False), (2, False), (3, False), (1, True), (5, True),
    ])
    def test_present_zero_returns_zero(self, total, is_half):
        assert calculate_payment(total, 0, is_half) == 0

    # isHalf → 250 * present (независимо от total)
    @pytest.mark.parametrize('total,present,expected', [
        (1, 1, 250),
        (2, 2, 500),
        (3, 1, 250),
        (3, 3, 750),
        (5, 2, 500),
    ])
    def test_half_lesson(self, total, present, expected):
        assert calculate_payment(total, present, is_half=True) == expected

    # Малая группа (total <= 2), все пришли → 500
    @pytest.mark.parametrize('total', [1, 2])
    def test_small_group_full(self, total):
        assert calculate_payment(total, total, is_half=False) == 500

    # Малая группа (total == 2), часть пришла → 300
    def test_small_group_partial(self):
        assert calculate_payment(2, 1, is_half=False) == 300

    # Одиночный ученик: total=1, present=1 → 500 (все пришли)
    def test_single_student_present(self):
        assert calculate_payment(1, 1, is_half=False) == 500

    # Большая группа (total > 2) → 200 * present
    @pytest.mark.parametrize('total,present,expected', [
        (3, 1, 200),
        (3, 2, 400),
        (3, 3, 600),
        (5, 0, 0),   # present=0 → 0 (already covered but consistent)
        (5, 4, 800),
        (10, 7, 1400),
    ])
    def test_per_student(self, total, present, expected):
        assert calculate_payment(total, present, is_half=False) == expected

    # Граничный случай: total=3, present=0 → 0
    def test_large_group_present_zero(self):
        assert calculate_payment(3, 0, is_half=False) == 0


# ---------------------------------------------------------------------------
# calculate_penalty
# ---------------------------------------------------------------------------

class TestCalculatePenalty:
    """Штраф: тот же день → 0, другой → 40 на присутствовавшего ученика."""

    def test_same_day_no_penalty(self):
        assert calculate_penalty('2026-06-10', '2026-06-10', 3) == 0

    def test_different_day_penalty_scales_with_present(self):
        assert calculate_penalty('2026-06-09', '2026-06-10', 1) == 40
        assert calculate_penalty('2026-06-09', '2026-06-10', 3) == 120

    def test_future_submit_penalty(self):
        # Урок в будущем, но дата не совпадает → штраф
        assert calculate_penalty('2026-06-11', '2026-06-10', 2) == 80

    def test_penalty_zero_present_no_penalty(self):
        # 0 присутствовавших → штраф 0 (нечего штрафовать)
        assert calculate_penalty('2026-01-01', '2026-06-10', 0) == 0


# ---------------------------------------------------------------------------
# calculate_extra_lesson_payment
# ---------------------------------------------------------------------------

class TestCalculateExtraLessonPayment:
    """Доп.урок: строго 200₽ за присутствовавшего, без half/small-group веток."""

    @pytest.mark.parametrize('present,expected', [
        (0, 0), (1, 200), (2, 400), (3, 600),
    ])
    def test_flat_rate_per_present_student(self, present, expected):
        assert calculate_extra_lesson_payment(present) == expected
