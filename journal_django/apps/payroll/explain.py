"""
explain.py — человекочитаемая расшифровка строки расчётного листа.

Зачем: преподаватель видит в кабинете сумму за урок и должен понимать, откуда
она взялась. Сумма без правила («800 ₽») порождает вопросы к администрации;
правило рядом с суммой («пришли 4 из 5 · 4 × 200 ₽») их снимает.

Ключевое решение: правило НЕ выводится из признаков урока «на глазок». Модуль
прогоняет тот же apps.payroll.calculator, что считал зарплату при записи урока,
и сверяет каждого кандидата с суммой, лежащей в БД. Совпало — отдаём формулу
этой ветки; не совпало ни с одной — значит сумму правил администратор
(PATCH /api/admin/payroll/:id), и мы честно говорим об этом, а не подгоняем
объяснение под число. Ставки берутся из PAY_RATES — второго источника правды
не появляется, при смене ставок расшифровка меняется вместе с расчётом.

Модуль чистый: ни ORM, ни запросов, ни Django. Тестируется в изоляции
(tests/test_explain.py).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from apps.payroll.calculator import (
    PAY_RATES, PENALTY_PER_STUDENT, calculate_extra_lesson_payment, calculate_payment,
)

# Типы уроков, которыми владеет apps.extra_lessons: доп.занятие и сгорание.
# Их ставка отличается от обычного занятия курса (флет 200 ₽), см. calculator.
_EXTRA_LESSON_TYPES = ('extra', 'burned')

_HALF_LESSON_MINUTES = 45

# Неразрывный пробел как разделитель разрядов: число не должно разрываться
# посреди себя при переносе строки на узком экране.
_NBSP = ' '


def _money(value: Decimal) -> str:
    """'800.00' → '800 ₽', '12500.00' → '12 500 ₽', '777.50' → '777,50 ₽'."""
    value = Decimal(value)
    whole = int(value.to_integral_value(rounding='ROUND_DOWN'))
    grouped = f'{whole:,}'.replace(',', _NBSP)
    remainder = value - whole
    if remainder:
        kopecks = f'{abs(remainder):.2f}'.split('.')[1]
        return f'{grouped},{kopecks} ₽'
    return f'{grouped} ₽'


def _plural(count: int, forms: tuple[str, str, str]) -> str:
    """Русская форма слова: (1 ученик, 2 ученика, 5 учеников)."""
    tail_100 = count % 100
    if 11 <= tail_100 <= 14:
        return forms[2]
    tail = count % 10
    if tail == 1:
        return forms[0]
    if 2 <= tail <= 4:
        return forms[1]
    return forms[2]


def _rule(code: str, text: str, note: str) -> dict:
    return {'code': code, 'text': text, 'note': note}


def _payment_candidates(
    total_students: int,
    present_count: int,
    duration_minutes: int,
    lesson_type: str,
) -> list[tuple[int, dict]]:
    """
    Кандидаты «сумма → объяснение» в порядке убывания вероятности. Первый, чья
    сумма совпала с сохранённой, и есть правило, по которому платили.
    """
    is_half = duration_minutes == _HALF_LESSON_MINUTES

    if present_count == 0:
        return [(0, _rule(
            'none', _money(Decimal(0)),
            'никто из учеников не был засчитан — оплата не начисляется',
        ))]

    if lesson_type in _EXTRA_LESSON_TYPES:
        # Отработка пропуска и сгорание — всегда флет-200 за пришедшего.
        flat = calculate_extra_lesson_payment(present_count)
        # Доп.занятие СВЕРХ курса у индивидуала оплачивается как обычный
        # индивидуальный урок (250 за 45 мин / 500 за полный), решение 2026-07-24.
        individual = calculate_payment(total=1, present=present_count, is_half=is_half)
        return [
            (flat, _rule(
                'extra_flat',
                f'{present_count} × {PAY_RATES["perStudent"]} ₽',
                f'дополнительное занятие — {PAY_RATES["perStudent"]} ₽ за каждого пришедшего',
            )),
            (individual, _rule(
                'extra_individual',
                _money(Decimal(individual)),
                'индивидуальное дополнительное занятие — оплата как за обычный '
                'индивидуальный урок',
            )),
        ]

    amount = calculate_payment(total_students, present_count, is_half)

    if is_half:
        rule = _rule(
            'half_lesson',
            f'{present_count} × {PAY_RATES["halfLesson"]} ₽',
            f'занятие 45 минут — {PAY_RATES["halfLesson"]} ₽ за каждого пришедшего',
        )
    elif total_students <= 2 and present_count == total_students:
        rule = _rule(
            'small_group_full',
            _money(Decimal(PAY_RATES['smallGroup'])),
            'занималось до 2 человек, пришли все',
        )
    elif total_students <= 2:
        rule = _rule(
            'small_group_partial',
            _money(Decimal(PAY_RATES['smallPartial'])),
            'занималось до 2 человек, пришли не все',
        )
    else:
        rule = _rule(
            'per_student',
            f'{present_count} × {PAY_RATES["perStudent"]} ₽',
            f'группа от 3 человек — {PAY_RATES["perStudent"]} ₽ за каждого пришедшего',
        )

    return [(amount, rule)]


def explain_payment(
    *,
    payment: Decimal,
    total_students: int,
    present_count: int,
    duration_minutes: int,
    lesson_type: str,
) -> dict:
    """
    Правило, по которому начислена выплата: {'code', 'text', 'note'}.

    code — машинный признак для фронта (подсветка/иконка), text — короткая
    формула в строке урока ('4 × 200 ₽'), note — расшифровка правила словами.
    """
    payment = Decimal(payment)
    for amount, rule in _payment_candidates(
        total_students, present_count, duration_minutes, lesson_type,
    ):
        if payment == Decimal(amount):
            return rule

    return _rule(
        'adjusted', _money(payment),
        'сумма скорректирована администратором',
    )


def explain_penalty(
    *,
    penalty: Decimal,
    present_count: int,
    lesson_date: datetime.date,
    submitted_at: Optional[datetime.datetime],
) -> Optional[str]:
    """
    Почему из выплаты удержали. None, если штрафа нет.

    Штраф начисляется, когда отчёт по уроку сдан не в день урока
    (calculate_penalty): PENALTY_PER_STUDENT ₽ за каждого присутствовавшего.
    submitted_at ожидается уже приведённым к МСК — форматирование дат тут,
    а не в вызывающем коде, чтобы вся формулировка жила в одном месте.
    """
    penalty = Decimal(penalty)
    if penalty == 0:
        return None

    formula = f'{PENALTY_PER_STUDENT} ₽ × {present_count}'
    if penalty != Decimal(PENALTY_PER_STUDENT * present_count):
        return 'удержание, изменено администратором'

    if submitted_at is None:
        return f'отчёт сдан не в день урока — {formula}'

    return (
        f'отчёт сдан {submitted_at.strftime("%d.%m")}, '
        f'урок был {lesson_date.strftime("%d.%m")} — {formula}'
    )


def explain_excluded(*, free: int, skip: int) -> Optional[str]:
    """
    Почему «пришли 4 из 4», когда в группе пятеро.

    total_students в payroll — это НЕ размер группы: неоплачиваемые пропуски
    (unpaid_skip) из headcount исключены (apps.lessons.services.record_lesson).
    free — бесплатные занятия, не учтённые в оплате: так считалось до
    2026-09-28, исторические уроки не пересчитывались. Без этой подписи
    расхождение выглядит как ошибка системы.
    """
    if not free and not skip:
        return None

    if free and skip:
        return (
            'не учтены в оплате: бесплатное занятие — '
            f'{free}, неоплачиваемый пропуск — {skip}'
        )

    count = free or skip
    reason = 'бесплатное занятие' if free else 'неоплачиваемый пропуск'
    noun = _plural(count, ('ученик', 'ученика', 'учеников'))
    verb = _plural(count, ('не учтён', 'не учтены', 'не учтены'))
    return f'{count} {noun} {verb} в оплате: {reason}'
