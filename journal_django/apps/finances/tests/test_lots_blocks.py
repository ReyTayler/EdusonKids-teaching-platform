"""
Построение партий FIFO из оплат (apps/finances/lots.py).

Партия без доплат = вся оплата (прежнее поведение, регресс).
Партия с доплатами дробится на абонементы по 4 урока — дорожает только свой блок.
См. docs/superpowers/specs/2026-07-28-course-surcharge-design.md.
"""
from __future__ import annotations

from decimal import Decimal

from apps.finances.lots import build_lots


def _row(pid, lessons, amount, kind='purchase', direction_id=7):
    return {
        'id': pid, 'lessons_count': lessons, 'total_amount': Decimal(amount),
        'kind': kind, 'direction_id': direction_id,
    }


def test_payment_without_surcharges_is_single_lot():
    """Без доплат — одна партия на оплату, цена как раньше."""
    lots = build_lots([_row(1, 36, '44000')], {})
    assert len(lots) == 1
    assert lots[0]['lessons'] == 36
    assert lots[0]['price_per_lesson'] == Decimal('44000') / Decimal('36')
    assert lots[0]['direction_id'] == 7


def test_extra_payment_has_no_direction():
    """Доплата сверх курса (kind='extra') в лимит направления не входила —
    партия остаётся без направления (прежнее правило, регресс)."""
    lots = build_lots([_row(1, 1, '1500', kind='extra')], {})
    assert lots[0]['direction_id'] is None


def test_zero_lessons_payment_skipped():
    """Guard: оплата без уроков партии не образует (деление на ноль)."""
    assert build_lots([_row(1, 0, '1000')], {}) == []


def test_surcharge_raises_price_of_its_block_only():
    """Доплата 1000 ₽ ко 2-му абонементу: дорожают только его 4 урока."""
    lots = build_lots([_row(1, 36, '44000')], {1: {2: Decimal('1000')}})
    assert len(lots) == 9                       # 36 уроков = 9 абонементов
    base = Decimal('44000') / Decimal('36')
    assert lots[0]['price_per_lesson'] == base  # 1-й блок — базовая цена
    assert lots[2]['price_per_lesson'] == base  # 3-й блок — базовая цена
    expected = (base * 4 + Decimal('1000')) / Decimal('4')
    assert lots[1]['price_per_lesson'] == expected


def test_total_money_preserved_with_surcharges():
    """Сумма по всем партиям = сумма оплаты + доплат, до копейки."""
    lots = build_lots([_row(1, 36, '44000')], {1: {2: Decimal('1000'), 5: Decimal('500')}})
    total = sum(lot['price_per_lesson'] * Decimal(lot['lessons']) for lot in lots)
    assert total == Decimal('44000') + Decimal('1000') + Decimal('500')


def test_incomplete_last_block():
    """Уроков не кратно 4 (предоплата) — последний блок неполный, деньги сходятся."""
    lots = build_lots([_row(1, 6, '6000')], {1: {2: Decimal('600')}})
    assert [lot['lessons'] for lot in lots] == [4, 2]
    total = sum(lot['price_per_lesson'] * Decimal(lot['lessons']) for lot in lots)
    assert total == Decimal('6600')


def test_surcharge_to_missing_block_is_ignored_safely():
    """Номер блока за пределами оплаты (битые данные) не роняет расчёт и не теряет
    уроки — деньги такой доплаты просто не попадают в партии."""
    lots = build_lots([_row(1, 4, '4000')], {1: {9: Decimal('100')}})
    assert [lot['lessons'] for lot in lots] == [4]
    assert lots[0]['price_per_lesson'] == Decimal('1000')


def test_build_lots_marks_every_lot_with_its_payment_id():
    """Партия знает свой платёж — на этом стоит реестр признания выручки."""
    lots = build_lots([_row(11, 4, '2000'), _row(12, 8, '4000')], {})
    assert [lot['payment_id'] for lot in lots] == [11, 12]


def test_build_lots_blocks_of_one_payment_share_payment_id():
    """Доплата дробит оплату на блоки — но все блоки принадлежат одному платежу."""
    lots = build_lots([_row(21, 8, '4000')], {21: {2: Decimal('500')}})
    assert len(lots) == 2
    assert {lot['payment_id'] for lot in lots} == {21}
