"""
Движок зарплат «Мотивации» — единственное место правил начисления
(спека docs/specs/2026-10-20-eduson-motivation-payroll-design.md, §1, §3).

Здесь только чистый расчёт без БД — rates_on / format_rate / lesson_pay /
cancellation_pay над простыми входными структурами (RateCard, LessonInput,
MarkerInput, PersonalRateInput): правила проверяются таблицей случаев. Сбор
входов из БД и запись начислений — apps/payroll/recalc.py.

Деньги — Decimal; округление до копеек ОДИН раз на выходе каждого начисления.
Промежуточная база отмены в пояснении печатается точно (без округления), чтобы
арифметика в тексте сходилась.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from apps.core.utils.dates import MSK

__all__ = [
    'RateCard', 'LessonInput', 'CancellationDecision', 'MarkerInput', 'PersonalRateInput', 'Result',
    'rates_on', 'format_rate', 'lesson_pay', 'cancellation_pay',
]

_KOPECK = Decimal('0.01')
_ZERO = Decimal('0.00')
_HUNDRED = Decimal('100')
# Неразрывный пробел в разрядах: сумма не рвётся переносом строки на узком экране.
_NBSP = ' '

# Источник записи «преподаватель сам в кабинете» — только такие уроки штрафуются
# за поздний отчёт (§1.4); админка ('admin-imported') и импорт ('eduson-import*') — нет.
_TEACHER_TOKEN_PREFIX = 'acct:'

# Сгорание: урок списан с ученика без занятия — преподаватель его не вёл (§1.10).
BURNED = 'burned'


# ---------------------------------------------------------------------------
# Входные структуры чистого расчёта
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RateCard:
    """Версия ставок школы в виде, удобном для расчёта (без ORM).

    direction_rates: {direction_id: (group_rate | None, individual_rate | None)};
    None в ячейке — «как по умолчанию»."""

    effective_from: datetime.date
    group_rate: Decimal
    individual_rate: Decimal
    cancel_mode: str
    cancel_value: Decimal
    penalty_enabled: bool = False
    penalty_mode: str = 'per_lesson'
    penalty_amount: Decimal = _ZERO
    direction_rates: dict = field(default_factory=dict)

    @classmethod
    def from_version(cls, version) -> 'RateCard':
        """PayRateVersion (directions уже prefetch'нуты) → RateCard."""
        return cls(
            effective_from=version.effective_from,
            group_rate=version.group_rate,
            individual_rate=version.individual_rate,
            cancel_mode=version.cancel_mode,
            cancel_value=version.cancel_value,
            penalty_enabled=version.penalty_enabled,
            penalty_mode=version.penalty_mode,
            penalty_amount=version.penalty_amount,
            direction_rates={
                d.direction_id: (d.group_rate, d.individual_rate)
                for d in version.directions.all()
            },
        )


@dataclass(frozen=True)
class LessonInput:
    teacher_id: int
    group_id: int
    lesson_date: datetime.date
    lesson_number: Decimal
    is_individual: bool
    direction_id: int | None
    direction_name: str | None
    submitted_at: datetime.datetime | None
    submitted_by_token: str | None
    present_count: int
    lesson_type: str = 'regular'


@dataclass(frozen=True)
class CancellationDecision:
    """Снимок решения об отмене (LessonCancellation.compensated) для маркера с записью
    (спека 2026-10-23-eduson-lesson-cancel-reasons-design, §4).

    reason — готовый текст причины (собирает apps/cancellations/repository.py):
    название из справочника либо «Другая причина» — свободный текст в пояснение не
    попадает, он может содержать ПДн. Движок текст не разбирает."""

    compensated: bool
    side_label: str
    reason: str


@dataclass(frozen=True)
class MarkerInput:
    """Маркер отмены (PlannedLesson status='cancelled', seq IS NULL)."""

    teacher_id: int
    group_id: int
    scheduled_date: datetime.date
    is_individual: bool
    direction_id: int | None
    direction_name: str | None
    # None — отмена без записи (до фичи причин, импорт старой системы): платится,
    # как раньше. Прошлое не пересматриваем.
    decision: CancellationDecision | None = None


@dataclass(frozen=True)
class PersonalRateInput:
    """Период персональной ставки (PersonalRateTerm, поправка §8).

    Действует на дату d, если valid_from ≤ d < valid_until; None — без границы.
    Пустой период (valid_from == valid_until) не действует ни на одну дату."""

    teacher_id: int
    group_id: int
    lesson_from: Decimal | None
    lesson_to: Decimal | None
    kind: str
    value: Decimal
    valid_from: datetime.date | None = None
    valid_until: datetime.date | None = None

    @property
    def is_all_lessons(self) -> bool:
        return self.lesson_from is None and self.lesson_to is None

    def covers(self, day: datetime.date) -> bool:
        return ((self.valid_from is None or self.valid_from <= day)
                and (self.valid_until is None or day < self.valid_until))


@dataclass(frozen=True)
class Result:
    payment: Decimal
    rule_note: str
    penalty: Decimal = _ZERO


# ---------------------------------------------------------------------------
# Форматирование пояснений (без ПДн: ни имён, ни ФИО — только правило и числа)
# ---------------------------------------------------------------------------

def _num(value: Decimal, exact: bool = False) -> str:
    """Число для пояснения: 1250.5 → «1 250,50», 500.00 → «500».

    exact=True — без округления до копеек (0.505 → «0,505»): так печатается
    промежуточная база, от которой дальше берётся процент."""
    value = Decimal(value)
    if not exact or value == value.quantize(_KOPECK):
        value = value.quantize(_KOPECK, rounding=ROUND_HALF_UP)
    sign = '-' if value < 0 else ''
    value = abs(value)
    whole = int(value)
    grouped = f'{whole:,}'.replace(',', _NBSP)
    fraction = value - whole
    if not fraction:
        return f'{sign}{grouped}'
    digits = format(fraction, 'f').split('.')[1].rstrip('0')
    return f'{sign}{grouped},{digits.ljust(2, "0")}'


def _rub(value: Decimal, exact: bool = False) -> str:
    return f'{_num(value, exact)} ₽'


def _lesson_no(value: Decimal) -> str:
    """Номер урока без лишних нулей: 10.0 → «10», 10.5 → «10.5»."""
    return format(Decimal(value).normalize(), 'f')


def _date(value: datetime.date) -> str:
    return value.strftime('%d.%m.%Y')


def _round(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_KOPECK, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Чистый расчёт
# ---------------------------------------------------------------------------

def rates_on(cards: Iterable[RateCard], day: datetime.date) -> RateCard | None:
    """Версия, действующая на дату: последняя с effective_from ≤ day."""
    best = None
    for card in cards:
        if card.effective_from <= day and (best is None
                                           or card.effective_from > best.effective_from):
            best = card
    return best


def _format_cell(card: RateCard, direction_id: int | None,
                 is_individual: bool) -> tuple[Decimal, bool]:
    """(ставка, взята ли из ячейки направления)."""
    cell = card.direction_rates.get(direction_id) if direction_id is not None else None
    if cell is not None:
        value = cell[1] if is_individual else cell[0]
        if value is not None:
            return value, True
    return (card.individual_rate if is_individual else card.group_rate), False


def format_rate(card: RateCard, direction_id: int | None, is_individual: bool) -> Decimal:
    """Ставка формата: ячейка направления, если задана, иначе «По умолчанию»."""
    return _format_cell(card, direction_id, is_individual)[0]


def _format_note(card: RateCard, direction_id, direction_name, is_individual) -> tuple[Decimal, str]:
    rate, from_direction = _format_cell(card, direction_id, is_individual)
    fmt = 'Индивидуальный урок' if is_individual else 'Групповой урок'
    if from_direction:
        head = f'{fmt} · {direction_name} · ставка с {_date(card.effective_from)}'
    else:
        head = f'{fmt} · ставка по умолчанию с {_date(card.effective_from)}'
    return rate, f'{head} — {_rub(rate)}'


def _apply_personal(base: Decimal, rate: PersonalRateInput,
                    exact: bool = False) -> tuple[Decimal, str]:
    """Персональная ставка поверх базы. Сумма НЕ округляется — округление одно, на выходе.
    exact — итог промежуточный (база отмены): печатается без округления."""
    if rate.is_all_lessons:
        scope = 'все уроки'
    else:
        scope = f'уроки {_lesson_no(rate.lesson_from)}–{_lesson_no(rate.lesson_to)}'
    if rate.kind == 'fixed':
        amount = rate.value
        tail = f'вместо ставки — {_rub(amount)}'
    elif rate.kind == 'plus_fixed':
        amount = base + rate.value
        tail = f'+{_rub(rate.value)} к {_rub(base)} = {_rub(amount, exact)}'
    elif rate.kind == 'plus_percent':
        amount = base * (1 + rate.value / _HUNDRED)
        tail = f'+{_num(rate.value)} % к {_rub(base)} = {_rub(amount, exact)}'
    else:  # CHECK в БД не пускает другие виды; защищаемся от тихого нуля
        raise ValueError(f'Неизвестный вид персональной ставки: {rate.kind!r}')
    return amount, f'Персональная ставка ({scope}): {tail}'


def _own_rates(personal_rates: Iterable[PersonalRateInput], teacher_id: int,
               group_id: int, day: datetime.date) -> list[PersonalRateInput]:
    # Персональная ставка действует, только когда занятие ведёт её преподаватель (§1.6),
    # и только периодами, действующими на дату занятия (§8): правка ставки сегодня
    # не меняет начислений за прошлые даты ни при каком пересчёте.
    return [r for r in personal_rates
            if r.teacher_id == teacher_id and r.group_id == group_id and r.covers(day)]


def _pick_lesson_rate(rates: list[PersonalRateInput],
                      number: Decimal) -> PersonalRateInput | None:
    """Диапазон, в который попал номер, точнее «все уроки» и побеждает его (§1.6)."""
    all_lessons = None
    for rate in rates:
        if rate.is_all_lessons:
            all_lessons = rate
        elif rate.lesson_from <= number <= rate.lesson_to:
            return rate
    return all_lessons


def _is_late_report(lesson: LessonInput) -> bool:
    """Отчёт преподавателя из кабинета не в день урока (дата submitted_at по МСК)."""
    token = lesson.submitted_by_token or ''
    if not token.startswith(_TEACHER_TOKEN_PREFIX) or lesson.submitted_at is None:
        return False
    return lesson.submitted_at.astimezone(MSK).date() > lesson.lesson_date


def lesson_pay(lesson: LessonInput, card: RateCard | None,
               personal_rates: Iterable[PersonalRateInput]) -> Result:
    """Начисление за проведённый урок по версии ставок на его дату."""
    if lesson.lesson_type == BURNED:
        return Result(_ZERO, 'Сгорание — не оплачивается', _ZERO)
    if card is None:
        return Result(_ZERO, f'Нет ставки на {_date(lesson.lesson_date)}', _ZERO)

    amount, note = _format_note(card, lesson.direction_id, lesson.direction_name,
                                lesson.is_individual)
    notes = [note]
    personal = _pick_lesson_rate(
        _own_rates(personal_rates, lesson.teacher_id, lesson.group_id, lesson.lesson_date),
        lesson.lesson_number)
    if personal is not None:
        amount, personal_note = _apply_personal(amount, personal)
        notes.append(personal_note)

    penalty = _ZERO
    if card.penalty_enabled and _is_late_report(lesson):
        if card.penalty_mode == 'per_student':
            penalty = card.penalty_amount * lesson.present_count
            penalty_note = (f'Штраф за отчёт не в день урока: '
                            f'{_rub(card.penalty_amount)} × {lesson.present_count}')
        else:
            penalty = card.penalty_amount
            penalty_note = f'Штраф за отчёт не в день урока: {_rub(card.penalty_amount)}'
        if penalty:
            notes.append(penalty_note)

    return Result(_round(amount), ' · '.join(notes), _round(penalty))


def cancellation_pay(marker: MarkerInput, card: RateCard | None,
                     personal_rates: Iterable[PersonalRateInput]) -> Result:
    """Начисление за отменённое занятие по версии на его дату (§1.3, §1.7).

    Решение об отмене (marker.decision) проверяется раньше ставки: «без
    компенсации» — ноль даже без версии ставок, а строка остаётся с причиной,
    чтобы в «Зарплате» был виден и ноль, и почему. С компенсацией — сумма
    «Мотивации» как прежде, причина — префиксом пояснения."""
    decision = marker.decision
    if decision is not None and not decision.compensated:
        return Result(_ZERO, f'Без компенсации: {decision.side_label} — {decision.reason}')
    res = _cancellation_amount(marker, card, personal_rates)
    if decision is None:
        return res
    return Result(res.payment, f'{decision.side_label} — {decision.reason} · {res.rule_note}')


def _cancellation_amount(marker: MarkerInput, card: RateCard | None,
                         personal_rates: Iterable[PersonalRateInput]) -> Result:
    """Сумма «Мотивации» за отмену: % от ставки формата (с персональной «все
    уроки») или фикс."""
    if card is None:
        return Result(_ZERO, f'Нет ставки на {_date(marker.scheduled_date)}')

    if card.cancel_mode == 'fixed':
        # Фикс не зависит ни от формата, ни от персональной ставки.
        return Result(_round(card.cancel_value), f'Отмена: фикс {_rub(card.cancel_value)}')

    base, note = _format_note(card, marker.direction_id, marker.direction_name,
                              marker.is_individual)
    notes = [note]
    # У отмены нет номера урока — применима только ставка «все уроки» (§1.7).
    personal = next((r for r in _own_rates(personal_rates, marker.teacher_id, marker.group_id,
                                           marker.scheduled_date)
                     if r.is_all_lessons), None)
    if personal is not None:
        base, personal_note = _apply_personal(base, personal, exact=True)
        notes.append(personal_note)
    amount = base * card.cancel_value / _HUNDRED
    notes.append(f'Отмена: {_num(card.cancel_value)} % от {_rub(base, exact=True)} '
                 f'= {_rub(amount)}')
    return Result(_round(amount), ' · '.join(notes))
