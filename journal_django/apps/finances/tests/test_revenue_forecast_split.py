"""
Юнит-тесты нарезки прогноза выручки по месяцам — чистые функции, БД не нужна.

Проверяют арифметику раскладки FIFO-хвоста по 4 урока в месяц и вспомогательные
хелперы месяцев. Работа с БД покрыта в apps/reports/tests/test_revenue_forecast.py
(там свежая test_eduson_test — прогноз читает всех учеников).

См. docs/superpowers/specs/2026-07-27-revenue-forecast-design.md
"""
from __future__ import annotations

from decimal import Decimal

from apps.finances.fifo import compute_fifo
from apps.finances.revenue_forecast import (
    _split_into_months,
    month_label,
    next_month,
)


def _D(x):
    return Decimal(str(x))


def _lot(lessons, price, direction_id=None):
    return {'lessons': _D(lessons), 'price_per_lesson': _D(price),
            'direction_id': direction_id}


# ---------------------------------------------------------------------------
# Хелперы месяцев
# ---------------------------------------------------------------------------

def test_next_month_rolls_over_the_year():
    assert next_month('2026-07') == '2026-08'
    assert next_month('2026-12') == '2027-01'


def test_month_label():
    assert month_label('2026-07') == 'Июль 2026'
    assert month_label('2027-01') == 'Январь 2027'


# ---------------------------------------------------------------------------
# Нарезка хвоста
# ---------------------------------------------------------------------------

def test_one_subscription_is_one_month():
    """4 урока = 1 абонемент = ровно один месяц."""
    by_month, lessons = _split_into_months([_lot(4, 1000)], '2026-07')

    assert by_month == {'2026-07': _D('4000.00')}
    assert lessons == _D('4')


def test_twelve_subscriptions_spread_over_twelve_months():
    """48 уроков по 2572.50 → 12 месяцев по 10290, с переходом через год."""
    by_month, lessons = _split_into_months([_lot(48, '2572.50')], '2026-07')

    assert len(by_month) == 12
    assert list(by_month)[0] == '2026-07'
    assert list(by_month)[-1] == '2027-06'
    assert set(by_month.values()) == {_D('10290.00')}
    assert sum(by_month.values(), _D(0)) == _D('123480.00')
    assert lessons == _D('48')


def test_partial_last_month():
    """Остаток меньше 4 уроков даёт неполный последний месяц."""
    by_month, lessons = _split_into_months([_lot(6, 1000)], '2026-07')

    assert by_month == {'2026-07': _D('4000.00'), '2026-08': _D('2000.00')}
    assert lessons == _D('6')


def test_month_spanning_two_lots_with_different_prices():
    """Месяц на границе партий считается по цене КАЖДОЙ партии, не по средней."""
    by_month, _ = _split_into_months([_lot(2, 1000), _lot(4, 500)], '2026-07')

    # Июль: 2 урока по 1000 + 2 урока по 500 = 3000. Август: 2 × 500 = 1000.
    assert by_month == {'2026-07': _D('3000.00'), '2026-08': _D('1000.00')}


def test_half_lesson_tail():
    """Half-lesson: остаток дробный, режется теми же 4 уроками в месяц."""
    by_month, lessons = _split_into_months([_lot('4.5', 1000)], '2026-07')

    assert by_month == {'2026-07': _D('4000.00'), '2026-08': _D('500.00')}
    assert lessons == _D('4.5')


def test_rounding_residue_goes_to_the_last_month():
    """Сумма месяцев в точности равна остатку — невязка уходит в последний месяц."""
    # Цена с третьим знаком: помесячное округление иначе разошлось бы с итогом.
    by_month, _ = _split_into_months([_lot(6, '333.333')], '2026-07')

    assert sum(by_month.values(), _D(0)) == _D('2000.00')   # 6 × 333.333 = 1999.998
    assert by_month['2026-07'] == _D('1333.33')
    assert by_month['2026-08'] == _D('666.67')


def test_empty_tail_gives_no_months():
    assert _split_into_months([], '2026-07') == ({}, _D('0'))


# ---------------------------------------------------------------------------
# Добор стартового месяца (часть его уже отработана фактом)
# ---------------------------------------------------------------------------

def test_start_month_is_topped_up_to_four_lessons():
    """Ученик уже сходил 2 урока в июле — из аванса в июль кладём только 2."""
    by_month, _ = _split_into_months([_lot(6, 1000)], '2026-07',
                                     first_month_capacity=_D(2))

    assert by_month == {'2026-07': _D('2000.00'), '2026-08': _D('4000.00')}


def test_full_start_month_pushes_plan_to_next_month():
    """Стартовый месяц выбран фактом целиком — прогноз начинается со следующего."""
    by_month, _ = _split_into_months([_lot(4, 1000)], '2026-07',
                                     first_month_capacity=_D(0))

    assert by_month == {'2026-08': _D('4000.00')}


def test_over_worked_start_month_also_pushes_plan():
    """Отработано больше 4 уроков — ёмкость отрицательная, июль пропускаем."""
    by_month, _ = _split_into_months([_lot(4, 1000)], '2026-07',
                                     first_month_capacity=_D(-2))

    assert by_month == {'2026-08': _D('4000.00')}


# ---------------------------------------------------------------------------
# remaining_lots из compute_fifo — вход нарезки
# ---------------------------------------------------------------------------

def test_compute_fifo_returns_unconsumed_tail_in_queue_order():
    """remaining_lots: непогашенный хвост по партиям, с ценой и направлением."""
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500), 'direction_id': 1},
        {'lessons': 4, 'price_per_lesson': _D(450), 'direction_id': 2},
    ]
    cons = [{'units': 1, 'date': '2026-06-10'} for _ in range(3)]

    r = compute_fifo(lots, cons, '2026-06-01', '2026-07-01')

    # Погашено 3 урока первой партии — в хвосте её остаток и вся вторая партия.
    assert r['remaining_lots'] == [
        {'lessons': _D(1), 'price_per_lesson': _D(500), 'direction_id': 1},
        {'lessons': _D(4), 'price_per_lesson': _D(450), 'direction_id': 2},
    ]
    # Хвост согласован с уже существующими агрегатами.
    assert r['remaining_value'] == _D('2300.00')
    assert sum(lot['lessons'] * lot['price_per_lesson']
               for lot in r['remaining_lots']) == _D('2300.00')


def test_compute_fifo_tail_is_empty_when_everything_consumed():
    lots = [{'lessons': 2, 'price_per_lesson': _D(500)}]
    cons = [{'units': 1, 'date': '2026-06-10'} for _ in range(2)]

    assert compute_fifo(lots, cons, '2026-06-01', '2026-07-01')['remaining_lots'] == []
