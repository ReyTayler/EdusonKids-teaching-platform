"""
Golden-тесты compute_fifo — дословный порт services/fifo.test.js на Decimal.

Каждый кейс сверяет Decimal-в-Decimal до копейки. Плюс граничные случаи из
брифа 02: half-lesson, перерасход, NULL-партий нет, переход месяца, частичное гашение.

compute_fifo — чистая функция, БД не нужна (нет pytest.mark.django_db).
"""
from __future__ import annotations

from decimal import Decimal

from apps.finances.fifo import compute_fifo

MS = '2026-06-01'
ME = '2026-07-01'


def _lessons(n, date):
    return [{'units': 1, 'date': date} for _ in range(n)]


def _D(x):
    return Decimal(str(x))


# ---------------------------------------------------------------------------
# Golden из fifo.test.js
# ---------------------------------------------------------------------------

def test_two_lots_across_month_boundary():
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(450)},
    ]
    cons = _lessons(3, '2026-05-10') + _lessons(4, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_total'] == _D('3350.00')
    assert r['worked_off_month'] == _D('1850.00')
    assert r['remaining_value'] == _D('450.00')
    assert r['over_consumed_lessons'] == _D('0.00')


def test_invariant_total_paid_equals_worked_plus_remaining():
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(450)},
    ]
    cons = _lessons(5, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    total_paid = _D(4 * 500 + 4 * 450)
    assert r['worked_off_total'] + r['remaining_value'] == total_paid


def test_half_lesson_partial():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = [{'units': 0.5, 'date': '2026-06-10'}]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_month'] == _D('250.00')
    assert r['remaining_value'] == _D('1750.00')


def test_over_consumed_no_price():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = _lessons(6, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_total'] == _D('2000.00')
    assert r['over_consumed_lessons'] == _D('2.00')
    assert r['remaining_value'] == _D('0.00')


def test_no_lots_all_over_consumed():
    r = compute_fifo([], _lessons(2, '2026-06-10'), MS, ME)
    assert r['worked_off_total'] == _D('0.00')
    assert r['over_consumed_lessons'] == _D('2.00')
    assert r['remaining_value'] == _D('0.00')


def test_no_consumption_all_remaining():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    r = compute_fifo(lots, [], MS, ME)
    assert r['worked_off_total'] == _D('0.00')
    assert r['remaining_value'] == _D('2000.00')


def test_worked_off_by_month():
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(450)},
    ]
    cons = _lessons(3, '2026-05-10') + _lessons(4, '2026-06-10')
    r = compute_fifo(lots, cons, '2026-06-01', '2026-07-01')
    assert r['worked_off_by_month']['2026-05'] == _D('1500.00')
    assert r['worked_off_by_month']['2026-06'] == _D('1850.00')


# ---------------------------------------------------------------------------
# Доп. граничные случаи (бриф 02)
# ---------------------------------------------------------------------------

def test_dec_to_jan_month_boundary():
    # Переход декабрь→январь: month_end эксклюзивный = '2026-01-01'.
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = _lessons(2, '2025-12-31') + _lessons(2, '2026-01-01')
    r = compute_fifo(lots, cons, '2025-12-01', '2026-01-01')
    # Только декабрьские уроки попадают в worked_off_month.
    assert r['worked_off_month'] == _D('1000.00')
    assert r['worked_off_total'] == _D('2000.00')
    assert r['worked_off_by_month']['2025-12'] == _D('1000.00')
    assert r['worked_off_by_month']['2026-01'] == _D('1000.00')


def test_fractional_price_per_lesson_kopeck_precision():
    # total_amount=6290 на 12 уроков → 524.1666... за урок. 12 уроков списано →
    # ровно 6290.00 (Decimal-точность, без накопленной float-ошибки).
    price = _D(6290) / _D(12)
    lots = [{'lessons': 12, 'price_per_lesson': price}]
    cons = _lessons(12, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_total'] == _D('6290.00')
    assert r['remaining_value'] == _D('0.00')


def test_partial_lot_consumption():
    # Частичное гашение партии: 2 из 4 уроков.
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = _lessons(2, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_total'] == _D('1000.00')
    assert r['remaining_value'] == _D('1000.00')


def test_worked_off_by_direction():
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(450)},
    ]
    cons = [
        {'units': 1, 'date': '2026-06-10', 'direction_id': 1},
        {'units': 1, 'date': '2026-06-11', 'direction_id': 1},
        {'units': 1, 'date': '2026-06-12', 'direction_id': 2},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_direction'][1] == _D('1000.00')
    assert r['worked_off_by_direction'][2] == _D('500.00')


def test_worked_off_by_direction_absent_key_is_ignored():
    # Golden-кейсы выше не передают direction_id — не должно падать, просто {}.
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = _lessons(2, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_direction'] == {}


def test_month_direction_cuts_diverge_when_lesson_pays_from_other_direction():
    # Урок направления 1 гасит партию направления 2 (общий пул на ученика):
    # разрез «по направлению УРОКА» и «по направлению ОПЛАТЫ» расходятся.
    lots = [{'lessons': 4, 'price_per_lesson': _D(500), 'direction_id': 2}]
    cons = [
        {'units': 1, 'date': '2026-06-10', 'direction_id': 1},
        {'units': 1, 'date': '2026-06-11', 'direction_id': 1},
        {'units': 1, 'date': '2026-07-05', 'direction_id': 1},
    ]
    r = compute_fifo(lots, cons, MS, ME)

    by_lesson = r['worked_off_by_month_lesson_direction']
    assert by_lesson[('2026-06', 1)] == {'value': _D('1000.00'), 'lessons': _D(2)}
    assert by_lesson[('2026-07', 1)] == {'value': _D('500.00'), 'lessons': _D(1)}

    by_lot = r['worked_off_by_month_lot_direction']
    assert by_lot[('2026-06', 2)] == {'value': _D('1000.00'), 'lessons': _D(2)}
    assert by_lot[('2026-07', 2)] == {'value': _D('500.00'), 'lessons': _D(1)}


def test_worked_off_by_month_lesson_direction_without_direction_key():
    # Направление урока может отсутствовать — ключ None, как у разреза по оплате.
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    r = compute_fifo(lots, _lessons(2, '2026-06-10'), MS, ME)
    assert r['worked_off_by_month_lesson_direction'] == {
        ('2026-06', None): {'value': _D('1000.00'), 'lessons': _D(2)},
    }


def test_refund_does_not_enter_lesson_direction_cut():
    # Возврат гасит партию, но выручкой не признаётся — ни в одном из разрезов.
    lots = [{'lessons': 4, 'price_per_lesson': _D(500), 'direction_id': 2}]
    cons = [{'units': 4, 'date': '2026-06-10', 'direction_id': 1, 'refund': True}]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_month_lesson_direction'] == {}
    assert r['worked_off_by_month_lot_direction'] == {}
    assert r['remaining_value'] == _D('0.00')


def test_worked_off_unit_prices_month_single_lot():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = _lessons(2, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_unit_prices_month'] == [_D('500.00')]


def test_worked_off_unit_prices_month_two_lots_crossed_within_month():
    # Партия A (500) на 3 урока заканчивается внутри месяца, продолжение — партия B (450).
    lots = [
        {'lessons': 3, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(450)},
    ]
    cons = _lessons(5, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_unit_prices_month'] == [_D('500.00'), _D('450.00')]


def test_worked_off_unit_prices_month_excludes_outside_month():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = _lessons(2, '2026-05-10')  # ДО месяца [MS, ME)
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_unit_prices_month'] == []


def test_worked_off_unit_prices_month_excludes_refund_and_over_consumption():
    lots = [{'lessons': 2, 'price_per_lesson': _D(500)}]
    cons = _lessons(4, '2026-06-10') + [  # 2 реальных + 2 сверх лимита (без партии)
        {'units': 1, 'date': '2026-06-15', 'refund': True},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    # Только цена реально списанной партии; ни возврат, ни перерасход не добавляют цену.
    assert r['worked_off_unit_prices_month'] == [_D('500.00')]


def test_worked_off_units_month_pairs_with_prices():
    # 5 уроков: 3 добивают лот A (500), 2 идут из лота B (450) — внутри месяца.
    lots = [
        {'lessons': 3, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(450)},
    ]
    cons = _lessons(5, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_unit_prices_month'] == [_D('500.00'), _D('450.00')]
    assert r['worked_off_units_month'] == [_D('3'), _D('2')]
    # Инвариант: sum(units[i] × price[i]) == worked_off_month.
    assert (_D('3') * _D('500') + _D('2') * _D('450')) == r['worked_off_month']


def test_worked_off_units_month_half_lesson_accumulates():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = [
        {'units': 0.5, 'date': '2026-06-10'},
        {'units': 1, 'date': '2026-06-11'},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_unit_prices_month'] == [_D('500.00')]
    assert r['worked_off_units_month'] == [_D('1.5')]  # 0.5 + 1 по одной цене


def test_worked_off_units_month_excludes_refund_and_over_consumption():
    lots = [{'lessons': 2, 'price_per_lesson': _D(500)}]
    cons = _lessons(4, '2026-06-10') + [
        {'units': 1, 'date': '2026-06-15', 'refund': True},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    # Только 2 урока покрыты партией; перерасход и возврат в кол-во не идут.
    assert r['worked_off_units_month'] == [_D('2')]


def test_refund_consumption_zeroes_remaining_without_revenue():
    from decimal import Decimal
    from apps.finances.fifo import compute_fifo
    lots = [{'lessons': 4, 'price_per_lesson': Decimal('1000')}]
    cons = [
        {'units': Decimal('1'), 'date': '2026-01-05', 'direction_id': None},
        {'units': Decimal('3'), 'date': '2026-01-31', 'direction_id': None, 'refund': True},
    ]
    r = compute_fifo(lots, cons, '2026-01-01', '2026-02-01')
    assert r['remaining_value'] == Decimal('0.00')      # хвост погашен возвратом
    assert r['worked_off_total'] == Decimal('1000.00')  # только 1 реальный урок


# ---------------------------------------------------------------------------
# remaining_by_direction — из каких направлений состоит непогашенный хвост.
# Нужен возврату средств: строка возврата пишется в то направление, чьи деньги
# реально возвращаются (иначе лимит курса не освобождается).
# ---------------------------------------------------------------------------

def test_remaining_by_direction_splits_tail_between_lots():
    # 5 уроков гасят лот A целиком (4) и один урок лота B → хвост = 3 урока лота B.
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500), 'direction_id': 1},
        {'lessons': 4, 'price_per_lesson': _D(450), 'direction_id': 2},
    ]
    r = compute_fifo(lots, _lessons(5, '2026-06-10'), MS, ME)
    assert r['remaining_by_direction'] == {2: {'lessons': _D('3'), 'value': _D('1350.00')}}


def test_remaining_by_direction_merges_lots_of_same_direction():
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500), 'direction_id': 7},
        {'lessons': 2, 'price_per_lesson': _D(300), 'direction_id': 7},
    ]
    r = compute_fifo(lots, _lessons(1, '2026-06-10'), MS, ME)
    assert r['remaining_by_direction'] == {7: {'lessons': _D('5'), 'value': _D('2100.00')}}


def test_remaining_by_direction_sums_to_remaining_value():
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500), 'direction_id': 1},
        {'lessons': 3, 'price_per_lesson': _D('333.33'), 'direction_id': 2},
    ]
    r = compute_fifo(lots, _lessons(2, '2026-06-10'), MS, ME)
    parts = r['remaining_by_direction']
    assert sum(b['lessons'] for b in parts.values()) == _D('5')
    assert sum(b['value'] for b in parts.values()) == r['remaining_value']


def test_remaining_by_direction_keeps_lots_without_direction():
    lots = [{'lessons': 2, 'price_per_lesson': _D(500), 'direction_id': None}]
    r = compute_fifo(lots, [], MS, ME)
    assert r['remaining_by_direction'] == {None: {'lessons': _D('2'), 'value': _D('1000.00')}}


def test_remaining_by_direction_empty_when_nothing_left():
    lots = [{'lessons': 2, 'price_per_lesson': _D(500), 'direction_id': 1}]
    r = compute_fifo(lots, _lessons(2, '2026-06-10'), MS, ME)
    assert r['remaining_by_direction'] == {}


# ---------------------------------------------------------------------------
# Разрезы по платежу (реестр признания выручки, спека 2026-09-08)
# ---------------------------------------------------------------------------

def test_worked_off_by_month_payment_splits_recognition_across_months():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500), 'payment_id': 7}]
    cons = _lessons(1, '2025-09-10') + _lessons(2, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_month_payment'] == {
        ('2025-09', 7): _D(500),
        ('2026-06', 7): _D(1000),
    }
    assert r['remaining_by_payment'] == {7: _D(500)}


def test_payment_slices_are_summed_into_one_payment_key():
    """Оплата с доплатой = две партии с разными ценами, но одним payment_id."""
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500), 'payment_id': 9},
        {'lessons': 4, 'price_per_lesson': _D(625), 'payment_id': 9},
    ]
    cons = _lessons(6, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_month_payment'] == {('2026-06', 9): _D(3250)}  # 4*500 + 2*625
    assert r['remaining_by_payment'] == {9: _D(1250)}                      # 2*625


def test_refund_fills_refunded_by_payment_and_not_revenue():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500), 'payment_id': 3}]
    cons = [
        {'units': 1, 'date': '2026-06-10'},
        {'units': 2, 'date': '2026-06-20', 'refund': True},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_month_payment'] == {('2026-06', 3): _D(500)}
    assert r['refunded_by_payment'] == {3: _D(1000)}
    assert r['remaining_by_payment'] == {3: _D(500)}


def test_refund_is_split_by_month_and_payment():
    """Возврат по месяцу ДАТЫ ВОЗВРАТА и по оплате, из партии которой вернули
    деньги: бухгалтерская сверка «аванс на конец = аванс на начало − отработано
    + оплачено − возвраты» требует возвратов именно этого месяца."""
    lots = [
        {'lessons': 2, 'price_per_lesson': _D(500), 'payment_id': 3},
        {'lessons': 4, 'price_per_lesson': _D(600), 'payment_id': 4},
    ]
    cons = [
        {'units': 1, 'date': '2026-05-10', 'refund': True},
        # Июньский возврат гасит остаток первой оплаты и захватывает вторую.
        {'units': 3, 'date': '2026-06-20', 'refund': True},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['refunded_by_month_payment'] == {
        ('2026-05', 3): _D(500),
        ('2026-06', 3): _D(500),
        ('2026-06', 4): _D(1200),
    }
    assert r['refunded_by_payment'] == {3: _D(1000), 4: _D(1200)}


def test_payment_cuts_are_exact_and_reconcile_with_lot_value():
    """Инвариант строки отчёта: стоимость партий = выручка + возврат + остаток."""
    lots = [{'lessons': 3, 'price_per_lesson': _D(1000) / _D(3), 'payment_id': 5}]
    cons = [
        {'units': 1, 'date': '2026-06-10'},
        {'units': 1, 'date': '2026-06-20', 'refund': True},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    revenue = sum(r['worked_off_by_month_payment'].values())
    # Каждый урок партии классифицирован ровно один раз: выручка, возврат или
    # непогашенный хвост. Сверяем с фактической стоимостью партии (цена задана
    # непериодической дробью нарочно — округления в разрезах быть не должно).
    lot_value = _D(3) * (_D(1000) / _D(3))
    assert revenue + r['refunded_by_payment'][5] + r['remaining_by_payment'][5] == lot_value


def test_half_lesson_recognition_lands_in_its_own_month():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500), 'payment_id': 4}]
    cons = [{'units': 0.5, 'date': '2026-05-10'}, {'units': 0.5, 'date': '2026-06-10'}]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_month_payment'] == {
        ('2026-05', 4): _D(250),
        ('2026-06', 4): _D(250),
    }


def test_lots_without_payment_id_are_skipped_in_payment_cuts():
    """Легаси-вызовы без payment_id должны работать как раньше, без падений."""
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    r = compute_fifo(lots, _lessons(2, '2026-06-10'), MS, ME)
    assert r['worked_off_by_month_payment'] == {}
    assert r['remaining_by_payment'] == {}
    assert r['refunded_by_payment'] == {}
    assert r['worked_off_month'] == _D('1000.00')


def test_over_consumed_value_prices_debt_by_last_lot():
    """Долг в деньгах: уроки сверх оплаченных считаем по цене последней партии."""
    lots = [
        {'lessons': 2, 'price_per_lesson': _D(500)},
        {'lessons': 2, 'price_per_lesson': _D(600)},
    ]
    r = compute_fifo(lots, _lessons(6, '2026-06-10'), MS, ME)
    assert r['over_consumed_lessons'] == _D('2.00')
    assert r['over_consumed_value'] == _D('1200.00')      # 2 урока × 600 (цена последней партии)


def test_over_consumed_value_is_zero_without_lots():
    """Без единой оплаты цены нет — долг деньгами посчитать не из чего."""
    r = compute_fifo([], _lessons(3, '2026-06-10'), MS, ME)
    assert r['over_consumed_lessons'] == _D('3.00')
    assert r['over_consumed_value'] == _D('0.00')


def test_no_debt_when_lots_cover_lessons():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    r = compute_fifo(lots, _lessons(3, '2026-06-10'), MS, ME)
    assert r['over_consumed_value'] == _D('0.00')


def test_lessons_and_remaining_lessons_by_payment():
    """Разбивка по оплате нужна не только в деньгах, но и в уроках."""
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500), 'payment_id': 1},
        {'lessons': 4, 'price_per_lesson': _D(600), 'payment_id': 2},
    ]
    cons = _lessons(2, '2026-05-10') + _lessons(4, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)

    # В июне отработано 2 урока из первой оплаты и 2 из второй.
    assert r['worked_off_lessons_by_month_payment'] == {
        ('2026-05', 1): _D(2),
        ('2026-06', 1): _D(2),
        ('2026-06', 2): _D(2),
    }
    assert r['remaining_lessons_by_payment'] == {2: _D(2)}
    assert r['remaining_by_payment'] == {2: _D(1200)}


def test_half_lesson_in_payment_lessons_cut():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500), 'payment_id': 8}]
    cons = [{'units': 0.5, 'date': '2026-06-10'}]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_lessons_by_month_payment'] == {('2026-06', 8): _D('0.5')}
    assert r['remaining_lessons_by_payment'] == {8: _D('3.5')}


def test_over_consumed_within_month_is_separated_from_lifetime():
    """Долг месяца считаем отдельно от накопленного: строка отчёта — про месяц."""
    lots = [{'lessons': 2, 'price_per_lesson': _D(500), 'payment_id': 1}]
    cons = _lessons(3, '2026-05-10') + _lessons(2, '2026-06-10')
    r = compute_fifo(lots, cons, MS, ME)

    assert r['over_consumed_lessons'] == _D('3.00')          # всего сверх оплаты
    assert r['over_consumed_lessons_month'] == _D('2.00')    # из них в июне
    assert r['over_consumed_value_month'] == _D('1000.00')   # 2 × 500


# ---------------------------------------------------------------------------
# Разрез по уроку — вкладка «Уроки» в карточке ученика (спека 2026-09-14)
# ---------------------------------------------------------------------------

def test_by_lesson_single_lesson():
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = [{'units': 1, 'date': '2026-06-10', 'lesson_id': 11}]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_lesson'] == {11: _D('500')}
    assert r['over_consumed_by_lesson'] == {}


def test_by_lesson_half_lesson_costs_half():
    """45 минут = 0.5 урока → списывается полцены партии."""
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = [{'units': 0.5, 'date': '2026-06-10', 'lesson_id': 12}]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_lesson'] == {12: _D('250.0')}


def test_by_lesson_spanning_two_lots_sums_both_parts():
    """Урок на стыке абонементов гасится двумя партиями — в разрезе сумма обеих.

    Партий в полурока не бывает (fifo_inputs: lessons = int(raw) > 0), поэтому
    на стык выводит half-lesson: 45-минутный урок сдвигает очередь на 0.5 и
    следующий целый урок садится сразу на две партии.
    """
    lots = [
        {'lessons': 2, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(400)},
    ]
    cons = [
        {'units': 0.5, 'date': '2026-06-10', 'lesson_id': 13},
        {'units': 1, 'date': '2026-06-11', 'lesson_id': 14},
        {'units': 1, 'date': '2026-06-12', 'lesson_id': 15},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    # Урок 15 добирает 0.5 из первой партии (250) и 0.5 из второй (200).
    assert r['worked_off_by_lesson'] == {
        13: _D('250.0'),
        14: _D('500'),
        15: _D('450.0'),
    }


def test_by_lesson_debt_lesson_has_no_money_and_is_over_consumed():
    """Урок сверх оплаченного: денег не признано, урок попал в перерасход."""
    lots = [{'lessons': 1, 'price_per_lesson': _D(500)}]
    cons = [
        {'units': 1, 'date': '2026-06-10', 'lesson_id': 21},
        {'units': 1, 'date': '2026-06-11', 'lesson_id': 22},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_lesson'] == {21: _D('500')}
    assert r['over_consumed_by_lesson'] == {22: _D('1')}


def test_by_lesson_partially_paid_lesson_is_both_recognized_and_debt():
    """Партий хватило на половину урока: полцены признано, полурока — долг.

    Остаток в полурока даёт не партия (таких не бывает), а предшествующий
    45-минутный урок, съевший половину целой партии.
    """
    lots = [{'lessons': 1, 'price_per_lesson': _D(500)}]
    cons = [
        {'units': 0.5, 'date': '2026-06-10', 'lesson_id': 30},
        {'units': 1, 'date': '2026-06-11', 'lesson_id': 31},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_lesson'] == {30: _D('250.0'), 31: _D('250.0')}
    assert r['over_consumed_by_lesson'] == {31: _D('0.5')}


def test_by_lesson_sum_equals_worked_off_total():
    """Разрез не расходится с итогом — иначе колонка врёт относительно отчётов."""
    lots = [
        {'lessons': 4, 'price_per_lesson': _D(500)},
        {'lessons': 4, 'price_per_lesson': _D(450)},
    ]
    cons = [
        {'units': 1, 'date': '2026-05-10', 'lesson_id': 41},
        {'units': 0.5, 'date': '2026-06-10', 'lesson_id': 42},
        {'units': 1, 'date': '2026-06-11', 'lesson_id': 43},
        {'units': 1, 'date': '2026-06-12', 'lesson_id': 44},
        {'units': 1, 'date': '2026-06-13', 'lesson_id': 45},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert sum(r['worked_off_by_lesson'].values()) == r['worked_off_total']
    # Итога мало: он сойдётся и если все деньги записать одному уроку. Урок 45
    # садится на стык партий (0.5 по 500 + 0.5 по 450) — проверяем адресность.
    assert r['worked_off_by_lesson'][45] == _D('475.0')


def test_by_lesson_ignores_consumptions_without_lesson_id():
    """Синтетический возврат урока не имеет — в разрезе его быть не должно."""
    lots = [{'lessons': 4, 'price_per_lesson': _D(500)}]
    cons = [
        {'units': 1, 'date': '2026-06-10', 'lesson_id': 51},
        {'units': 3, 'date': '2026-06-20', 'direction_id': None, 'refund': True},
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_lesson'] == {51: _D('500')}
    assert r['over_consumed_by_lesson'] == {}


def test_by_lesson_debt_without_lesson_id_is_ignored():
    """Легаси-вызовы (dashboard/reports/student_month) урока не передают.

    Перерасход всё так же копится в общий over_consumed_lessons, но в разрез
    по уроку не попадает — иначе ключ None собрал бы долг всех таких записей.
    """
    lots = [{'lessons': 1, 'price_per_lesson': _D(500)}]
    cons = [
        {'units': 1, 'date': '2026-06-10', 'lesson_id': 61},
        {'units': 1, 'date': '2026-06-11'},  # сверх оплаченного и без урока
    ]
    r = compute_fifo(lots, cons, MS, ME)
    assert r['worked_off_by_lesson'] == {61: _D('500')}
    assert r['over_consumed_by_lesson'] == {}
    assert r['over_consumed_lessons'] == _D('1.00')
