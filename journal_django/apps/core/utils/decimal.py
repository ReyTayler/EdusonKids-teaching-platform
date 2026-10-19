"""
Decimal / financial arithmetic utilities for journal_django.

Rules:
- Always go through str() when converting to Decimal (never float → Decimal directly).
- Round to kopecks (2 decimal places) with ROUND_HALF_UP.
- Mirrors the precision conventions in services/calculator.js.
"""
from __future__ import annotations

import decimal
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

_KOPECK = Decimal('0.01')
_HALF = Decimal('0.5')
_HUNDRED = Decimal('100')


def to_decimal(x) -> Decimal:
    """
    Safely convert x to Decimal via string representation.

    Avoids float binary-representation errors (e.g. 1.1 + 2.2 != 3.3 in float).
    Accepts: int, float, str, Decimal.

    Raises:
        decimal.InvalidOperation if x cannot be converted.
    """
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def round_kopecks(value) -> Decimal:
    """
    Round *value* to kopecks (2 decimal places) using ROUND_HALF_UP.

    Args:
        value: anything accepted by to_decimal().

    Returns:
        Decimal quantized to '0.01'.
    """
    return to_decimal(value).quantize(_KOPECK, rounding=ROUND_HALF_UP)


def js_round2(value) -> Decimal:
    """
    Round *value* to 2 decimals reproducing JS `Math.round(x*100)/100` EXACTLY.

    JS Math.round округляет половину к +∞ (Math.round(-2.5) === -2), тогда как
    ROUND_HALF_UP (round_kopecks) округляет половину от нуля (-2.5 → -3). Для
    дашборда (services/repo/dashboard.js _round2 = Math.round(x*100)/100) нужна
    именно JS-семантика, чтобы отрицательные балансы совпадали байт-в-байт.

    Реальные входы дашборда (суммы копеечных Decimal, балансы кратны 0.5) на
    уровне x*100 целые, поэтому округление фактически no-op; но семантика точная.
    """
    scaled = to_decimal(value) * _HUNDRED
    floor_part = scaled.to_integral_value(rounding=ROUND_FLOOR)
    n = floor_part + 1 if (scaled - floor_part) >= _HALF else floor_part
    return n / _HUNDRED


def js_number(value) -> int | float:
    """
    Повторяет JS Number(x) для JSON-вывода: целое → int, дробное → float.

    Decimal('8.0') → 8, Decimal('7.5') → 7.5, Decimal('15039.00') → 15039.
    Нужно там, где Express отдаёт ВЫЧИСЛЕННЫЕ числа как JSON-numbers (дашборд,
    баланс), а не сырые numeric-колонки (те идут строкой через renderer).
    """
    f = float(value)
    return int(f) if f == int(f) else f
