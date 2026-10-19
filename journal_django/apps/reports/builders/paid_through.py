"""
Построитель отчёта «Оплачено до»: по каждому ученику базы — на каком курсе он
сейчас и до какого числа оплачено обучение.

Строка — ученик (все ученики базы, включая ушедших и без оплат). Три правила:

  • «Курс сейчас» — направления групп, где у ученика АКТИВНОЕ членство в
    АКТИВНОЙ группе. Второе условие не лишнее: группы, восстановленные из
    истории, неактивны, а членство в них осталось активным. Записан в группу,
    которая ещё не начала занятия, — это тоже «курс сейчас» (к нему приступит).
    Две группы — курсы через запятую в одной строке: баланс у ученика общий.
  • Остаток — общий баланс ученика по всем направлениям (purchased − attended,
    45 минут = 0.5 урока), та же формула, что в карточке ученика.
  • «4 урока = 1 месяц» (решение пользователя 2026-10-05, как в прогнозе
    отработки денег): месяцев = остаток / 4; дата = сегодня + целые
    календарные месяцы + по неделе на каждый оставшийся урок. Остаток ≤ 0 —
    обучение не оплачено: 0 месяцев, даты нет.
  • «Месяцев обучения» — у ВСЕХ (уточнение пользователя 2026-10-05).
    «Оплачено до» — ТОЛЬКО тем, кто сейчас учится: есть курс сейчас И
    последняя сделка продления не на стадии «Заморожен» и не в исходе «Ушёл».
    Заморозка и уход членства не снимают (спека 2026-07-25), поэтому одной
    группы мало. Остальным дата пустая: оплата не расходуется, срок неизвестен.
"""
from __future__ import annotations

import datetime
import io
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta

from django.db.models import OuterRef, Subquery

from apps.core.utils.dates import msk_now
from apps.finances.repository import balances_for_students
from apps.memberships.models import GroupMembership
from apps.renewals.models import RenewalDeal, RenewalStage
from apps.renewals.transitions import FROZEN_KEY
from apps.students.models import Student

LESSONS_PER_MONTH = Decimal('4')
DAYS_PER_LESSON = 7
ZERO = Decimal('0')


@dataclass
class PaidThroughRow:
    student_id: int
    platform_id: str | None
    full_name: str
    courses: str
    balance: Decimal
    months: Decimal
    paid_until: datetime.date | None  # None — не оплачено или сейчас не учится


def paid_until(balance: Decimal, today: datetime.date) -> tuple[Decimal, datetime.date | None]:
    """(месяцев обучения, оплачено до) по правилу «4 урока = 1 месяц».

    Целые месяцы прибавляются календарно (31.01 + 1 месяц = 28.02), остаток
    уроков — по неделе на урок, пол-урока округляется вверх до целого дня.
    """
    if balance <= ZERO:
        return ZERO, None
    full_months = int(balance // LESSONS_PER_MONTH)
    rest_lessons = balance - full_months * LESSONS_PER_MONTH
    rest_days = int((rest_lessons * DAYS_PER_LESSON).to_integral_value(rounding=ROUND_CEILING))
    until = today + relativedelta(months=full_months) + datetime.timedelta(days=rest_days)
    months = (balance / LESSONS_PER_MONTH).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return months.normalize(), until


def _current_courses() -> dict[int, str]:
    """student_id → направления активных групп ученика через запятую."""
    by_student: dict[int, set[str]] = defaultdict(set)
    rows = (
        GroupMembership.objects
        .filter(active=True, group__active=True)
        .values_list('student_id', 'group__direction__name')
    )
    for student_id, direction_name in rows:
        by_student[student_id].add(direction_name)
    return {sid: ', '.join(sorted(names)) for sid, names in by_student.items()}


def _is_studying(has_course: bool, stage_key: str | None, stage_kind: str | None) -> bool:
    """Учится сейчас: есть курс сейчас и последняя сделка не «Заморожен»/«Ушёл».
    Нет сделок вовсе (новичок) — решает только курс."""
    return has_course and stage_key != FROZEN_KEY and stage_kind != RenewalStage.Kind.LOST


def collect(today: datetime.date) -> list[PaidThroughRow]:
    """Все ученики базы, по возрастанию ID. Три запроса: ученики (со стадией
    последней сделки), членства, баланс."""
    # Стадия ПОСЛЕДНЕЙ сделки (max cycle_no) — «статус» ученика; тот же
    # коррелированный подзапрос, что в apps.students.repository (Index Scan по
    # UNIQUE (student_id, cycle_no)).
    latest = RenewalDeal.objects.filter(student_id=OuterRef('pk')).order_by('-cycle_no')
    students = list(
        Student.objects
        .annotate(stage_key=Subquery(latest.values('stage__key')[:1]),
                  stage_kind=Subquery(latest.values('stage__kind')[:1]))
        .order_by('id')
        .values('id', 'full_name', 'platform_id', 'stage_key', 'stage_kind')
    )
    courses = _current_courses()
    balances = balances_for_students([s['id'] for s in students])

    rows = []
    for s in students:
        # balances_for_students отдаёт int|float — в Decimal через str, без хвостов float.
        balance = Decimal(str(balances[s['id']]))
        course = courses.get(s['id'], '')
        months, until = paid_until(balance, today)
        if not _is_studying(bool(course), s['stage_key'], s['stage_kind']):
            until = None
        rows.append(PaidThroughRow(
            student_id=s['id'],
            platform_id=s['platform_id'],
            full_name=s['full_name'],
            courses=course,
            balance=balance,
            months=months,
            paid_until=until,
        ))
    return rows


HEADERS = [
    'ID ученика', 'Platform ID', 'Ученик', 'Курс сейчас',
    'Остаток уроков', 'Месяцев обучения', 'Оплачено до',
]
_COLUMN_WIDTHS = {1: 12, 2: 16, 3: 32, 4: 30, 5: 16, 6: 18, 7: 16}


def build_workbook(rows: list[PaidThroughRow], today: datetime.date):
    """openpyxl.Workbook: плоский лист без строк итогов (под автофильтр)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'Оплачено до на {today:%d.%m.%Y}'

    ws.append(HEADERS)
    thin = Side(style='thin', color='D9D9D9')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill('solid', fgColor='EDEDED')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = border

    center = Alignment(horizontal='center')
    for r_idx, row in enumerate(rows, start=2):
        values = [
            row.student_id, row.platform_id or '', row.full_name, row.courses,
            # float, а не Decimal: иначе Excel не посчитает по колонке сам.
            float(row.balance), float(row.months), row.paid_until,
        ]
        for c_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            cell.border = border
            if c_idx in (1, 5, 6, 7):
                cell.alignment = center
        # Кратно 0.5 / до сотых: «6», «1,5», «0,13» — без хвостов «6,00».
        ws.cell(row=r_idx, column=5).number_format = '0.#'
        ws.cell(row=r_idx, column=6).number_format = '0.##'
        ws.cell(row=r_idx, column=7).number_format = 'DD.MM.YYYY'

    for col_idx, width in _COLUMN_WIDTHS.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.freeze_panes = ws.cell(row=2, column=1)
    ws.auto_filter.ref = f'A1:{get_column_letter(len(HEADERS))}{max(len(rows) + 1, 2)}'
    return wb


def build() -> tuple[bytes, int, str]:
    """(xlsx-байты, число учеников, имя файла). Отчёт строится на сегодня (МСК)."""
    today = msk_now().date()
    rows = collect(today)
    buf = io.BytesIO()
    build_workbook(rows, today).save(buf)
    return buf.getvalue(), len(rows), f'paid_through_{today.isoformat()}.xlsx'
