"""
PayrollService — тонкий слой между views и repository. Никакого SQL здесь.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.utils import timezone

from apps.core.utils.dates import msk_month_range
from apps.payroll import repository
from apps.payroll.explain import explain_excluded, explain_payment, explain_penalty

# Названия месяцев для подписи периода. Явная таблица, а не локаль Django:
# настраивать локализацию сервера ради одной подписи — лишняя связанность.
_MONTH_LABELS = (
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)


def list_payroll(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = 'lesson_date',
    sort_dir: str = 'desc',
    filters: Optional[dict] = None,
) -> dict:
    return repository.list_payroll(
        page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir, filters=filters,
    )


def payroll_summary(
    teacher_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    return repository.payroll_summary(teacher_id=teacher_id, date_from=date_from, date_to=date_to)


def update_payroll(payroll_id: int, fields: dict) -> Optional[dict]:
    return repository.update_payroll(payroll_id, fields)


# ---------------------------------------------------------------------------
# Кабинет преподавателя — своя зарплата за месяц
# ---------------------------------------------------------------------------

def _money(value: Decimal) -> str:
    """Деньги строкой с масштабом ('800.00') — контракт teacher SPA."""
    return f'{Decimal(value):.2f}'


def my_payroll_month(teacher_id: int, month: str) -> dict:
    """
    Зарплата ОДНОГО преподавателя за месяц 'YYYY-MM' с расшифровкой каждой строки.

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

        rule = explain_payment(
            payment=payment,
            total_students=e['total_students'],
            present_count=e['present_count'],
            duration_minutes=e['duration_minutes'],
            lesson_type=e['lesson_type'],
        )
        submitted_at = e['submitted_at']
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
            'date': e['lesson_date'],
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
            'penaltyNote': explain_penalty(
                penalty=penalty,
                present_count=e['present_count'],
                lesson_date=e['lesson_date'],
                # submitted_at хранится в UTC (USE_TZ=True) — приводим к МСК,
                # иначе поздний вечерний отчёт покажет вчерашнюю дату.
                submitted_at=timezone.localtime(submitted_at) if submitted_at else None,
            ),
            'excludedNote': explain_excluded(
                free=free_unpaid, skip=marks.get('skip', 0),
            ),
            'adjusted': rule['code'] == 'adjusted',
        })

    year, month_number = int(month[:4]), int(month[5:7])

    return {
        'month': month,
        'monthLabel': f'{_MONTH_LABELS[month_number - 1]} {year}',
        'totals': {
            'lessons': len(rows),
            'presences': presences,
            'payment': _money(sum_payment),
            'penalty': _money(sum_penalty),
            'net': _money(sum_payment - sum_penalty),
        },
        'rows': rows,
    }
