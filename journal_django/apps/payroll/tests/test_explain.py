"""
Unit-тесты apps/payroll/explain.py — расшифровки выплаты для кабинета
преподавателя. Модуль чистый (без БД), поэтому маркер django_db не нужен.

Смысл расшифровки: преподаватель должен видеть не только сумму, но и правило,
по которому она получилась. Правило не «угадывается» по признакам урока —
explain прогоняет тот же calculator и сверяет с суммой из БД, поэтому ручная
правка администратора честно помечается как adjusted, а не выдаётся за формулу.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from apps.payroll.explain import explain_excluded, explain_payment, explain_penalty


def _explain(payment, total, present, duration=60, lesson_type='regular'):
    return explain_payment(
        payment=Decimal(payment),
        total_students=total,
        present_count=present,
        duration_minutes=duration,
        lesson_type=lesson_type,
    )


# ---------------------------------------------------------------------------
# explain_payment — ветки обычного занятия
# ---------------------------------------------------------------------------

def test_per_student_group_of_three_or_more():
    """Группа 3+: 200 ₽ за каждого пришедшего."""
    r = _explain('800.00', total=5, present=4)
    assert r['code'] == 'per_student'
    assert r['text'] == '4 × 200 ₽'
    assert 'за каждого пришедшего' in r['note']


def test_small_group_full_attendance():
    """До 2 человек, пришли все → 500 ₽ фиксированно."""
    r = _explain('500.00', total=2, present=2)
    assert r['code'] == 'small_group_full'
    assert r['text'] == '500 ₽'


def test_small_group_partial_attendance():
    """До 2 человек, пришли не все → 300 ₽."""
    r = _explain('300.00', total=2, present=1)
    assert r['code'] == 'small_group_partial'
    assert r['text'] == '300 ₽'


def test_half_lesson_pays_per_student_and_beats_group_size():
    """45 минут → 250 ₽ за каждого, независимо от размера группы."""
    r = _explain('1000.00', total=5, present=4, duration=45)
    assert r['code'] == 'half_lesson'
    assert r['text'] == '4 × 250 ₽'
    assert '45' in r['note']


def test_individual_full_lesson_is_small_group_full():
    """Индивидуальное занятие 60 мин — это «малая группа, пришли все» = 500 ₽."""
    r = _explain('500.00', total=1, present=1)
    assert r['code'] == 'small_group_full'


# ---------------------------------------------------------------------------
# explain_payment — доп.уроки и сгорания
# ---------------------------------------------------------------------------

def test_extra_lesson_flat_rate():
    """Доп.занятие/отработка — флет 200 ₽ за каждого пришедшего."""
    r = _explain('200.00', total=1, present=1, lesson_type='extra')
    assert r['code'] == 'extra_flat'
    assert r['text'] == '1 × 200 ₽'
    assert 'дополнительное занятие' in r['note']


def test_burned_lesson_flat_rate():
    """Сгоревшее занятие оплачивается так же — флет 200 ₽."""
    r = _explain('200.00', total=1, present=1, lesson_type='burned')
    assert r['code'] == 'extra_flat'


def test_extra_individual_over_course_pays_as_regular_individual():
    """Доп.урок сверх курса для индива = обычный индив-урок: 500 ₽ (60 мин)."""
    r = _explain('500.00', total=1, present=1, lesson_type='extra')
    assert r['code'] == 'extra_individual'
    assert r['text'] == '500 ₽'


def test_extra_individual_half_duration():
    """То же, но 45 минут → 250 ₽."""
    r = _explain('250.00', total=1, present=1, duration=45, lesson_type='extra')
    assert r['code'] == 'extra_individual'
    assert r['text'] == '250 ₽'


# ---------------------------------------------------------------------------
# explain_payment — граничные случаи
# ---------------------------------------------------------------------------

def test_nobody_present_pays_nothing():
    r = _explain('0.00', total=4, present=0)
    assert r['code'] == 'none'
    assert r['text'] == '0 ₽'
    assert 'не начисляется' in r['note']


def test_headcount_zero_after_exclusions():
    """Все присутствовавшие исключены из headcount (free/пропуск) → 0 ₽."""
    r = _explain('0.00', total=0, present=0)
    assert r['code'] == 'none'


def test_manual_correction_is_reported_honestly():
    """Сумма не сходится ни с одной формулой → это правка администратора."""
    r = _explain('777.00', total=5, present=4)
    assert r['code'] == 'adjusted'
    assert r['text'] == '777 ₽'
    assert 'администратором' in r['note']


def test_manual_correction_with_kopecks_is_formatted_ru():
    r = _explain('777.50', total=5, present=4)
    assert r['code'] == 'adjusted'
    assert r['text'] == '777,50 ₽'


def test_thousands_are_grouped_without_line_break():
    """Крупная правка не должна разрываться посреди числа при переносе строки."""
    r = _explain('12500.00', total=5, present=4)
    assert r['text'] == '12 500 ₽'


def test_nobody_present_but_payment_nonzero_is_adjusted():
    """present=0 при ненулевой сумме — тоже ручная правка, не «никто не пришёл»."""
    r = _explain('200.00', total=4, present=0)
    assert r['code'] == 'adjusted'


# ---------------------------------------------------------------------------
# explain_penalty
# ---------------------------------------------------------------------------

def test_no_penalty_returns_none():
    assert explain_penalty(
        penalty=Decimal('0'), present_count=4,
        lesson_date=datetime.date(2026, 7, 3), submitted_at=None,
    ) is None


def test_penalty_explains_both_dates_and_formula():
    note = explain_penalty(
        penalty=Decimal('160.00'), present_count=4,
        lesson_date=datetime.date(2026, 7, 3),
        submitted_at=datetime.datetime(2026, 7, 5, 12, 0),
    )
    assert '05.07' in note and '03.07' in note
    assert '40 ₽ × 4' in note


def test_penalty_without_submitted_at_still_explains_rule():
    note = explain_penalty(
        penalty=Decimal('160.00'), present_count=4,
        lesson_date=datetime.date(2026, 7, 3), submitted_at=None,
    )
    assert 'не в день урока' in note
    assert '40 ₽ × 4' in note


def test_penalty_not_matching_formula_is_marked_manual():
    note = explain_penalty(
        penalty=Decimal('99.00'), present_count=4,
        lesson_date=datetime.date(2026, 7, 3), submitted_at=None,
    )
    assert 'администратором' in note


# ---------------------------------------------------------------------------
# explain_excluded — почему «пришли 4 из 4» в группе из пяти человек
# ---------------------------------------------------------------------------

def test_no_exclusions_returns_none():
    assert explain_excluded(free=0, skip=0) is None


def test_single_free_student():
    note = explain_excluded(free=1, skip=0)
    assert note == '1 ученик не учтён в оплате: бесплатное занятие'


def test_two_free_students_plural():
    note = explain_excluded(free=2, skip=0)
    assert note == '2 ученика не учтены в оплате: бесплатное занятие'


def test_five_skipped_students_plural():
    note = explain_excluded(free=0, skip=5)
    assert note == '5 учеников не учтены в оплате: неоплачиваемый пропуск'


def test_both_reasons_are_listed_separately():
    note = explain_excluded(free=1, skip=2)
    assert 'бесплатное занятие — 1' in note
    assert 'неоплачиваемый пропуск — 2' in note
