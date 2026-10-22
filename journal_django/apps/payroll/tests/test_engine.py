"""
Движок зарплат «Мотивации» (спека 2026-10-20-eduson-motivation-payroll-design, §1, §3, §7).

Чистый расчёт (lesson_pay / cancellation_pay / rates_on / format_rate) —
таблица случаев без БД: формат × направление, версии по датам, персональные
ставки, отмены, штраф, сгорание, округление. Пересчёт поверх БД — test_recalc.py.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.payroll.engine import (
    LessonInput,
    MarkerInput,
    PersonalRateInput,
    RateCard,
    cancellation_pay,
    format_rate,
    lesson_pay,
    rates_on,
)

UTC = datetime.timezone.utc
D = Decimal

PY_DIR = 7      # направление со своими ставками
EMPTY_DIR = 8   # направление без строки в версии → «По умолчанию»
NULL_DIR = 9    # строка есть, ячейки пустые → «По умолчанию»
TEACHER = 11
OTHER_TEACHER = 12
GROUP = 21


def _card(effective_from=datetime.date(2026, 6, 1), **overrides) -> RateCard:
    fields = {
        'effective_from': effective_from,
        'group_rate': D('600'),
        'individual_rate': D('800'),
        'direction_rates': {
            PY_DIR: (D('500'), D('1000')),
            NULL_DIR: (None, None),
        },
        'cancel_mode': 'percent',
        'cancel_value': D('50'),
        'penalty_enabled': False,
        'penalty_mode': 'per_lesson',
        'penalty_amount': D('0'),
    }
    fields.update(overrides)
    return RateCard(**fields)


def _lesson(**overrides) -> LessonInput:
    fields = {
        'teacher_id': TEACHER,
        'group_id': GROUP,
        'lesson_date': datetime.date(2026, 6, 10),
        'lesson_number': D('12'),
        'is_individual': False,
        'direction_id': PY_DIR,
        'direction_name': 'Python',
        # 12:00 МСК того же дня
        'submitted_at': datetime.datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
        'submitted_by_token': 'acct:5',
        'present_count': 3,
    }
    fields.update(overrides)
    return LessonInput(**fields)


def _marker(**overrides) -> MarkerInput:
    fields = {
        'teacher_id': TEACHER,
        'group_id': GROUP,
        'scheduled_date': datetime.date(2026, 6, 10),
        'is_individual': False,
        'direction_id': PY_DIR,
        'direction_name': 'Python',
    }
    fields.update(overrides)
    return MarkerInput(**fields)


def _personal(kind, value, lesson_from=None, lesson_to=None,
              teacher_id=TEACHER, group_id=GROUP, valid_from=None,
              valid_until=None) -> PersonalRateInput:
    return PersonalRateInput(
        teacher_id=teacher_id, group_id=group_id,
        lesson_from=None if lesson_from is None else D(str(lesson_from)),
        lesson_to=None if lesson_to is None else D(str(lesson_to)),
        kind=kind, value=D(str(value)), valid_from=valid_from, valid_until=valid_until,
    )


# ---------------------------------------------------------------------------
# Ставка формата: формат × направление
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('direction_id, is_individual, expected', [
    (PY_DIR, False, D('500')),      # своя ставка направления, групповой
    (PY_DIR, True, D('1000')),      # своя ставка направления, индивидуальный
    (EMPTY_DIR, False, D('600')),   # направления нет в версии → по умолчанию
    (EMPTY_DIR, True, D('800')),
    (NULL_DIR, False, D('600')),    # ячейка пустая → по умолчанию
    (NULL_DIR, True, D('800')),
    (None, False, D('600')),        # у группы нет направления
])
def test_format_rate_direction_cell_or_default(direction_id, is_individual, expected):
    assert format_rate(_card(), direction_id, is_individual) == expected


def test_format_rate_half_filled_direction_falls_back_per_cell():
    # Заполнен только групповой формат — индивидуальный берётся из «По умолчанию».
    card = _card(direction_rates={PY_DIR: (D('550'), None)})
    assert format_rate(card, PY_DIR, False) == D('550')
    assert format_rate(card, PY_DIR, True) == D('800')


def test_lesson_pay_group_direction_note():
    res = lesson_pay(_lesson(), _card(), [])
    assert res.payment == D('500.00')
    assert res.penalty == D('0.00')
    assert res.rule_note == 'Групповой урок · Python · ставка с 01.06.2026 — 500 ₽'


def test_lesson_pay_individual_default_note():
    res = lesson_pay(_lesson(is_individual=True, direction_id=EMPTY_DIR,
                             direction_name='Roblox'), _card(), [])
    assert res.payment == D('800.00')
    assert res.rule_note == 'Индивидуальный урок · ставка по умолчанию с 01.06.2026 — 800 ₽'


def test_lesson_pay_ignores_present_count():
    # §1.1: число пришедших на ставку не влияет.
    one = lesson_pay(_lesson(present_count=1), _card(), [])
    many = lesson_pay(_lesson(present_count=9), _card(), [])
    assert one.payment == many.payment == D('500.00')


# ---------------------------------------------------------------------------
# Версии по датам
# ---------------------------------------------------------------------------

def test_rates_on_picks_latest_not_after_date():
    v1 = _card(datetime.date(2026, 6, 1), group_rate=D('600'))
    v2 = _card(datetime.date(2026, 9, 1), group_rate=D('700'))
    cards = [v2, v1]  # порядок входа не важен
    assert rates_on(cards, datetime.date(2026, 5, 31)) is None
    assert rates_on(cards, datetime.date(2026, 6, 1)) is v1
    assert rates_on(cards, datetime.date(2026, 8, 31)) is v1
    assert rates_on(cards, datetime.date(2026, 9, 1)) is v2
    assert rates_on(cards, datetime.date(2027, 1, 1)) is v2
    assert rates_on([], datetime.date(2026, 6, 1)) is None


def test_lesson_without_version_is_zero_with_note():
    res = lesson_pay(_lesson(lesson_date=datetime.date(2026, 3, 5)), None, [])
    assert res.payment == D('0.00')
    assert res.penalty == D('0.00')
    assert res.rule_note == 'Нет ставки на 05.03.2026'


def test_cancellation_without_version_is_zero_with_note():
    res = cancellation_pay(_marker(scheduled_date=datetime.date(2026, 3, 5)), None, [])
    assert res.payment == D('0.00')
    assert res.rule_note == 'Нет ставки на 05.03.2026'


# ---------------------------------------------------------------------------
# Персональные ставки
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('kind, value, expected, tail', [
    ('fixed', 900, D('900.00'), 'вместо ставки — 900 ₽'),
    ('plus_fixed', 150, D('650.00'), '+150 ₽ к 500 ₽ = 650 ₽'),
    ('plus_percent', 20, D('600.00'), '+20 % к 500 ₽ = 600 ₽'),
])
def test_personal_all_lessons(kind, value, expected, tail):
    res = lesson_pay(_lesson(), _card(), [_personal(kind, value)])
    assert res.payment == expected
    assert res.rule_note.endswith(f'Персональная ставка (все уроки): {tail}')


@pytest.mark.parametrize('kind, value, expected', [
    ('fixed', 900, D('900.00')),
    ('plus_fixed', 150, D('650.00')),
    ('plus_percent', 20, D('600.00')),
])
@pytest.mark.parametrize('number, applies', [
    (D('9.5'), False), (D('10'), True), (D('12.5'), True), (D('15'), True), (D('15.5'), False),
])
def test_personal_range_bounds_inclusive(kind, value, expected, number, applies):
    res = lesson_pay(_lesson(lesson_number=number), _card(),
                     [_personal(kind, value, 10, 15)])
    assert res.payment == (expected if applies else D('500.00'))
    assert ('уроки 10–15' in res.rule_note) is applies


def test_personal_range_beats_all_lessons():
    rates = [_personal('fixed', 900), _personal('plus_percent', 20, 10, 15)]
    inside = lesson_pay(_lesson(lesson_number=D('11')), _card(), rates)
    assert inside.payment == D('600.00')
    assert inside.rule_note.endswith(
        'Персональная ставка (уроки 10–15): +20 % к 500 ₽ = 600 ₽')
    outside = lesson_pay(_lesson(lesson_number=D('16')), _card(), rates)
    assert outside.payment == D('900.00')


def test_personal_range_label_fractional_numbers():
    res = lesson_pay(_lesson(lesson_number=D('10.5')), _card(),
                     [_personal('plus_fixed', 100, D('10.5'), D('12.0'))])
    assert 'Персональная ставка (уроки 10.5–12)' in res.rule_note


def test_personal_of_other_teacher_or_group_ignored():
    rates = [_personal('fixed', 900, teacher_id=OTHER_TEACHER),
             _personal('fixed', 999, group_id=GROUP + 1)]
    res = lesson_pay(_lesson(), _card(), rates)
    assert res.payment == D('500.00')
    assert 'Персональная' not in res.rule_note


def test_personal_percent_rounding_once_half_up():
    # 500 × 1.3333 = 666.65 ровно; 601 × 1.3333 = 801.3133 → 801.31;
    # 0.005 копейки — ROUND_HALF_UP: 0.5 × 1.01 = 0.505 → 0.51.
    res = lesson_pay(_lesson(), _card(), [_personal('plus_percent', '33.33')])
    assert res.payment == D('666.65')
    card = _card(direction_rates={PY_DIR: (D('601'), None)})
    assert lesson_pay(_lesson(), card, [_personal('plus_percent', '33.33')]).payment == D('801.31')
    tiny = _card(direction_rates={PY_DIR: (D('0.50'), None)})
    assert lesson_pay(_lesson(), tiny, [_personal('plus_percent', 1)]).payment == D('0.51')


# ---------------------------------------------------------------------------
# Отмена
# ---------------------------------------------------------------------------

def test_cancellation_percent_of_format_rate():
    res = cancellation_pay(_marker(), _card(), [])
    assert res.payment == D('250.00')
    assert res.rule_note.endswith('Отмена: 50 % от 500 ₽ = 250 ₽')


def test_cancellation_percent_individual_default():
    res = cancellation_pay(_marker(is_individual=True, direction_id=EMPTY_DIR), _card(), [])
    assert res.payment == D('400.00')


def test_cancellation_fixed():
    res = cancellation_pay(_marker(), _card(cancel_mode='fixed', cancel_value=D('200')),
                           [_personal('fixed', 900)])
    assert res.payment == D('200.00')
    assert res.rule_note.endswith('Отмена: фикс 200 ₽')


@pytest.mark.parametrize('kind, value, expected', [
    ('fixed', 900, D('450.00')),
    ('plus_fixed', 100, D('300.00')),
    ('plus_percent', 20, D('300.00')),
])
def test_cancellation_percent_uses_personal_all_lessons(kind, value, expected):
    res = cancellation_pay(_marker(), _card(), [_personal(kind, value)])
    assert res.payment == expected
    assert 'Персональная ставка (все уроки)' in res.rule_note


def test_cancellation_ignores_range_and_foreign_personal():
    # §1.7: у отмены нет номера урока — диапазонная ставка не применяется;
    # ставка другого преподавателя тоже.
    rates = [_personal('fixed', 900, 1, 100),
             _personal('fixed', 900, teacher_id=OTHER_TEACHER)]
    res = cancellation_pay(_marker(), _card(), rates)
    assert res.payment == D('250.00')


# Периоды персональных ставок (поправка §8): действует период, содержащий дату
# занятия — valid_from ≤ дата < valid_until; вне периода — ставка формата.
_DAY = datetime.date(2026, 6, 10)


@pytest.mark.parametrize('valid_from,valid_until,applies', [
    (None, None, True),
    (_DAY, None, True),                                   # начало включительно
    (None, _DAY, False),                                  # конец НЕ включительно
    (None, _DAY + datetime.timedelta(days=1), True),
    (_DAY + datetime.timedelta(days=1), None, False),
    (_DAY, _DAY, False),                                  # пустой период
])
def test_personal_term_period_bounds(valid_from, valid_until, applies):
    rate = _personal('fixed', 900, valid_from=valid_from, valid_until=valid_until)
    lesson = lesson_pay(_lesson(lesson_date=_DAY), _card(), [rate])
    marker = cancellation_pay(_marker(scheduled_date=_DAY), _card(), [rate])
    assert (lesson.payment == D('900.00')) is applies
    assert (marker.payment == D('450.00')) is applies


def test_personal_range_beats_all_lessons_only_within_dates():
    """Диапазон побеждает «все уроки» среди периодов, действующих на дату;
    закрытый диапазон уступает действующей «все уроки»."""
    rates = [_personal('fixed', 900),
             _personal('fixed', 1500, 1, 20, valid_until=_DAY)]
    assert lesson_pay(_lesson(lesson_date=_DAY), _card(), rates).payment == D('900.00')
    assert lesson_pay(_lesson(lesson_date=_DAY - datetime.timedelta(days=1)), _card(),
                      rates).payment == D('1500.00')


def test_cancellation_rounding_once():
    # База 0.50 + 1 % = 0.505 (не округляется); 50 % → 0.2525 → 0.25.
    # Двойное округление дало бы 0.51 × 50 % = 0.255 → 0.26.
    card = _card(direction_rates={PY_DIR: (D('0.50'), None)})
    res = cancellation_pay(_marker(), card, [_personal('plus_percent', 1)])
    assert res.payment == D('0.25')


# ---------------------------------------------------------------------------
# Штраф за поздний отчёт
# ---------------------------------------------------------------------------

_PENALTY_ON = {'penalty_enabled': True, 'penalty_amount': D('40')}
# 2026-06-10 урок; 21:30 UTC 10.06 = 00:30 МСК 11.06 — уже следующий день.
_SAME_DAY_LATE = datetime.datetime(2026, 6, 10, 20, 59, tzinfo=UTC)   # 23:59 МСК 10.06
_NEXT_DAY_EARLY = datetime.datetime(2026, 6, 10, 21, 30, tzinfo=UTC)  # 00:30 МСК 11.06


@pytest.mark.parametrize('mode, expected', [
    ('per_lesson', D('40.00')),
    ('per_student', D('120.00')),
])
def test_penalty_teacher_cabinet_next_day(mode, expected):
    card = _card(penalty_mode=mode, **_PENALTY_ON)
    res = lesson_pay(_lesson(submitted_at=_NEXT_DAY_EARLY, present_count=3), card, [])
    assert res.payment == D('500.00')
    assert res.penalty == expected
    if mode == 'per_student':
        assert res.rule_note.endswith('Штраф за отчёт не в день урока: 40 ₽ × 3')
    else:
        assert res.rule_note.endswith('Штраф за отчёт не в день урока: 40 ₽')


def test_penalty_same_msk_day_not_charged():
    card = _card(penalty_mode='per_lesson', **_PENALTY_ON)
    res = lesson_pay(_lesson(submitted_at=_SAME_DAY_LATE), card, [])
    assert res.penalty == D('0.00')
    assert 'Штраф' not in res.rule_note


@pytest.mark.parametrize('token', ['admin-imported', 'eduson-import', 'eduson-import:x',
                                   'manual-override', ''])
def test_penalty_only_for_teacher_cabinet(token):
    card = _card(penalty_mode='per_lesson', **_PENALTY_ON)
    res = lesson_pay(_lesson(submitted_at=_NEXT_DAY_EARLY, submitted_by_token=token), card, [])
    assert res.penalty == D('0.00')


def test_penalty_disabled_by_default():
    card = _card(penalty_amount=D('40'))  # penalty_enabled=False
    res = lesson_pay(_lesson(submitted_at=_NEXT_DAY_EARLY), card, [])
    assert res.penalty == D('0.00')


def test_penalty_uses_version_of_lesson_date():
    # Нет версии на дату урока → и штрафа нет, даже если отчёт поздний.
    res = lesson_pay(_lesson(submitted_at=_NEXT_DAY_EARLY), None, [])
    assert res.penalty == D('0.00')


def test_penalty_per_student_zero_present():
    card = _card(penalty_mode='per_student', **_PENALTY_ON)
    res = lesson_pay(_lesson(submitted_at=_NEXT_DAY_EARLY, present_count=0), card, [])
    assert res.penalty == D('0.00')


def test_money_formatting_thousands_and_kopecks():
    card = _card(direction_rates={PY_DIR: (D('1250.5'), None)})
    res = lesson_pay(_lesson(), card, [])
    assert res.rule_note == 'Групповой урок · Python · ставка с 01.06.2026 — 1 250,50 ₽'


# ---------------------------------------------------------------------------
# Сгорание и пояснение отмены
# ---------------------------------------------------------------------------

def test_burned_lesson_not_paid_even_with_personal_and_penalty():
    card = _card(penalty_mode='per_student', **_PENALTY_ON)
    res = lesson_pay(_lesson(lesson_type='burned', submitted_at=_NEXT_DAY_EARLY), card,
                     [_personal('fixed', 900)])
    assert (res.payment, res.penalty) == (D('0.00'), D('0.00'))
    assert res.rule_note == 'Сгорание — не оплачивается'


def test_cancellation_note_shows_exact_base():
    # База 0.505 в тексте — без округления, чтобы «50 % от 0,505 = 0,25» сходилось.
    card = _card(direction_rates={PY_DIR: (D('0.50'), None)})
    res = cancellation_pay(_marker(), card, [_personal('plus_percent', 1)])
    assert res.rule_note.endswith(
        'Персональная ставка (все уроки): +1 % к 0,50 ₽ = 0,505 ₽ · '
        'Отмена: 50 % от 0,505 ₽ = 0,25 ₽')
