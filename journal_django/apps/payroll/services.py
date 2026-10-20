"""
PayrollService — тонкий слой между views и repository. Никакого SQL здесь.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from apps.core.utils.dates import msk_month_range
from apps.payroll import repository
from apps.payroll.explain import explain_excluded

# Названия месяцев для подписи периода. Явная таблица, а не локаль Django:
# настраивать локализацию сервера ради одной подписи — лишняя связанность.
_MONTH_LABELS = (
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)


def payroll_summary(
    teacher_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    return repository.payroll_summary(teacher_id=teacher_id, date_from=date_from, date_to=date_to)


def update_payroll(payroll_id: int, fields: dict) -> Optional[dict]:
    return repository.update_payroll(payroll_id, fields)


def reset_payroll(payroll_id: int) -> Optional[dict]:
    return repository.reset_payroll(payroll_id)


def update_cancellation(cancellation_id: int, payment: Decimal) -> Optional[dict]:
    return repository.update_cancellation(cancellation_id, payment)


def reset_cancellation(cancellation_id: int) -> Optional[dict]:
    return repository.reset_cancellation(cancellation_id)


# ---------------------------------------------------------------------------
# Кабинет преподавателя — своя зарплата за месяц
# ---------------------------------------------------------------------------

def _money(value: Decimal) -> str:
    """Деньги строкой с масштабом ('800.00') — контракт teacher SPA."""
    return f'{Decimal(value):.2f}'


_MIDNIGHT = datetime.time(0, 0)

# Подписи ручной правки: сумма задана администратором, правило движка её не
# объясняет — говорим как есть, а расчётная сумма идёт рядом (calcPayment).
_MANUAL_NOTE = 'Сумма изменена администратором'
_MANUAL_PENALTY_NOTE = 'Удержание задано администратором'


def _rule(e: dict, payment: Decimal) -> dict:
    """
    Пояснение начисления для строки кабинета. Источник — Payroll.rule_note,
    записанный движком зарплат (apps/payroll/engine.py); вторую расшифровку
    ставок здесь не строим.

    Форма {'code', 'text', 'note'} сохранена ради совместимости с кабинетом:
    code — 'manual' (ручная правка) или 'calc' (расчёт движка), text — сумма,
    note — правило словами.
    """
    if e['is_manual']:
        note = f"{_MANUAL_NOTE} · расчёт: {_money(e['calc_payment'])} ₽"
        if e['rule_note']:
            note = f"{note} · {e['rule_note']}"
        return {'code': 'manual', 'text': f'{_money(payment)} ₽', 'note': note}
    return {'code': 'calc', 'text': f'{_money(payment)} ₽', 'note': e['rule_note']}


def my_payroll_month(teacher_id: int, month: str) -> dict:
    """
    Зарплата ОДНОГО преподавателя за месяц 'YYYY-MM' с пояснением каждой строки
    (rule_note движка зарплат; ручная правка помечена и несёт расчётную сумму).

    teacher_id приходит из JWT (вьюха), не из запроса — иначе преподаватель
    прочитал бы чужую зарплату.

    Итоги считаются в Decimal по уже выбранным строкам: отдельный SQL-агрегат
    ради трёх сумм на три десятка строк не нужен, а расхождение «итог не равен
    сумме строк» при таком подходе невозможно по построению.
    """
    date_from, date_to = msk_month_range(f'{month}-01')
    entries = repository.teacher_month_entries(teacher_id, date_from, date_to)
    excluded = repository.excluded_headcount([e['lesson_id'] for e in entries])

    rows: list[dict] = []
    sum_payment = Decimal('0')
    sum_penalty = Decimal('0')
    presences = 0

    for e in entries:
        payment = Decimal(e['payment'])
        penalty = Decimal(e['penalty'])
        net = payment - penalty

        sum_payment += payment
        sum_penalty += penalty
        presences += e['present_count']

        rule = _rule(e, payment)
        # Штраф движок объясняет внутри rule_note (сегмент «Штраф за отчёт…»);
        # отдельная строка штрафа в кабинете получает то же пояснение целиком.
        penalty_note = None
        if penalty:
            penalty_note = _MANUAL_PENALTY_NOTE if e['is_manual'] else e['rule_note']
        marks = excluded.get(e['lesson_id'], {})
        # Бесплатные не учтены в оплате только на уроках до 2026-09-28 (тогда
        # is_free исключался из present_count). Признак — present_count меньше
        # числа реально присутствовавших; на новых уроках разницы нет.
        free_unpaid = min(
            marks.get('free', 0),
            max(0, marks.get('attended', 0) - e['present_count']),
        )

        rows.append({
            'lessonId': e['lesson_id'],
            # Единая форма строк с отменами (у урока времени в журнале нет).
            'cancellationId': None,
            'date': e['lesson_date'],
            'time': None,
            'group': e['group_name'],
            'direction': e['direction_name'],
            'directionColor': e['direction_color'],
            'lessonNumber': str(e['lesson_number']),
            'kind': e['lesson_type'],
            'durationMinutes': e['duration_minutes'],
            'totalStudents': e['total_students'],
            'presentCount': e['present_count'],
            'payment': _money(payment),
            'penalty': _money(penalty),
            'net': _money(net),
            'rule': rule,
            'ruleNote': e['rule_note'],
            'penaltyNote': penalty_note,
            'excludedNote': explain_excluded(
                free=free_unpaid, skip=marks.get('skip', 0),
            ),
            'adjusted': e['is_manual'],
            'isManual': e['is_manual'],
            'calcPayment': _money(e['calc_payment']),
            'calcPenalty': _money(e['calc_penalty']),
        })

    lessons_count = len(rows)

    # Отмены (§1.3): своя строка с kind='cancellation' — значения kind у уроков
    # (lesson_type: regular/extra/…) с ним не пересекаются, контракт строк уроков
    # не меняется. Полей урока (номер, состав, длительность) у отмены нет — null.
    cancellations = repository.teacher_month_cancellations(teacher_id, date_from, date_to)
    for c in cancellations:
        payment = Decimal(c['payment'])
        sum_payment += payment
        rows.append({
            'lessonId': None,
            'cancellationId': c['id'],
            'date': c['lesson_date'],
            'time': c['scheduled_time'],
            'group': c['group_name'],
            'direction': c['direction_name'],
            'directionColor': c['direction_color'],
            'lessonNumber': None,
            'kind': 'cancellation',
            'durationMinutes': None,
            'totalStudents': None,
            'presentCount': None,
            'payment': _money(payment),
            'penalty': _money(Decimal('0')),
            'net': _money(payment),
            'rule': _rule(c, payment),
            'ruleNote': c['rule_note'],
            'penaltyNote': None,
            'excludedNote': None,
            'adjusted': c['is_manual'],
            'isManual': c['is_manual'],
            'calcPayment': _money(c['calc_payment']),
            'calcPenalty': _money(Decimal('0')),
        })
    # Новые сверху по (дата, время); у урока времени нет — в пределах дня он
    # после отмен со временем. sort стабилен: уроки одного дня сохраняют
    # прежний порядок (-id).
    rows.sort(key=lambda r: (r['date'], r['time'] is not None, r['time'] or _MIDNIGHT),
              reverse=True)

    year, month_number = int(month[:4]), int(month[5:7])

    return {
        'month': month,
        'monthLabel': f'{_MONTH_LABELS[month_number - 1]} {year}',
        'totals': {
            'lessons': lessons_count,
            'cancellations': len(cancellations),
            'presences': presences,
            'payment': _money(sum_payment),
            'penalty': _money(sum_penalty),
            'net': _money(sum_payment - sum_penalty),
        },
        'rows': rows,
    }
