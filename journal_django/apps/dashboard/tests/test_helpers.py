"""
Unit-тесты чистых хелперов дашборда (БД не нужна).

js_round2  — мост к JS Math.round(x*100)/100 (важна семантика отрицательных половин).
js_number  — JS Number(): целое→int, дробное→float (формат JSON-вывода дашборда).
_add_day   — +1 день для эксклюзивного to.
msk_month_range_triple — порт mskMonthRange, переход Dec→Jan.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from apps.core.utils.decimal import js_number, js_round2
from apps.dashboard.services import _add_day


def test_js_round2_positive_half_up():
    assert js_round2(Decimal('2.345')) == Decimal('2.35')
    assert js_round2(Decimal('2.344')) == Decimal('2.34')


def test_js_round2_negative_half_toward_pos_inf():
    # JS Math.round(-2.5)=-2 (к +∞), НЕ -3 (как ROUND_HALF_UP). Проверяем на уровне x*100.
    assert js_round2(Decimal('-0.025')) == Decimal('-0.02')   # -2.5 → -2
    assert js_round2(Decimal('-0.026')) == Decimal('-0.03')   # -2.6 → -3


def test_js_round2_noop_on_exact_2dp():
    assert js_round2(Decimal('429830.15')) == Decimal('429830.15')
    assert js_round2(Decimal('0')) == Decimal('0')


def test_js_number_whole_is_int():
    assert js_number(Decimal('15039.00')) == 15039
    assert isinstance(js_number(Decimal('15039.00')), int)
    assert js_number(Decimal('-23')) == -23
    assert isinstance(js_number(Decimal('-23')), int)


def test_js_number_fractional_is_float():
    assert js_number(Decimal('1553570.50')) == 1553570.5
    assert isinstance(js_number(Decimal('1553570.50')), float)
    assert js_number(Decimal('-2.5')) == -2.5


def test_add_day():
    assert _add_day('2026-06-10') == '2026-06-11'
    # Переход через границу месяца/года
    assert _add_day('2026-06-30') == '2026-07-01'
    assert _add_day('2025-12-31') == '2026-01-01'


def test_msk_month_range_triple_dec_to_jan():
    from apps.core.utils.dates import msk_month_range_triple
    # Декабрь → month_end эксклюзивно переносит год.
    dec = datetime.datetime(2026, 12, 10, 12, 0, tzinfo=datetime.timezone.utc)
    month, start, end = msk_month_range_triple(dec)
    assert month == '2026-12'
    assert start == '2026-12-01'
    assert end == '2027-01-01'


def test_msk_month_range_triple_mid_year():
    from apps.core.utils.dates import msk_month_range_triple
    jun = datetime.datetime(2026, 6, 15, 12, 0, tzinfo=datetime.timezone.utc)
    month, start, end = msk_month_range_triple(jun)
    assert (month, start, end) == ('2026-06', '2026-06-01', '2026-07-01')
