"""
«Бухгалтерский отчёт» по ученикам за месяц + запись в Excel.

Строка = ученик × ОПЛАТА: детальная разбивка (решение пользователя 2026-09-10).
Отдельная строка появляется на каждую оплату, деньги которой отрабатывались в
месяце, и на каждую оплату, пришедшую в месяце. Поэтому ученик, который часть
уроков месяца отработал давней оплатой, а часть — оплатой этого месяца, даёт
несколько строк с разной стоимостью урока. Ученик без движения — одна пустая
строка (в отчёте есть ВСЕ ученики базы).

Что в строке:
  • «Посещено» — уроки месяца, оплаченные ИМЕННО этой оплатой (списавшие баланс;
    бесплатное занятие ничего не списывает и в счёт не идёт);
  • «Отработано» — деньги этой оплаты, отработанные в месяце;
  • «Стоимость 1 урока» — цена урока этой оплаты (с учётом доплат к абонементу);
  • «Итого оплачено за месяц» — сумма оплаты, ЕСЛИ она пришла в выбранном месяце;
    у давней оплаты здесь 0, но аванс всё равно показывается;
  • «Возврат за месяц» — деньги этой оплаты, возвращённые клиенту в месяце
    (по дате возврата). Нужен бухгалтерской сверке (решение пользователя
    2026-10-05): аванс на конец = аванс на конец прошлого месяца − отработано +
    оплачено − возврат. Возврат уменьшает аванс, но выручкой не становится;
  • «Остаток оплаченных уроков» / «Остаток аванса» — непогашенный хвост ЭТОЙ
    оплаты на конец месяца. Оплата с остатком даёт строку даже без движения в
    месяце, поэтому колонка суммируется в аванс всей школы на отчётную дату;
  • «Долг» — деньги за уроки сверх оплаченных. Такие уроки не привязаны ни к
    одной оплате, поэтому им отводится отдельная строка ученика.

ОТЧЁТ — СНИМОК НА КОНЕЦ ВЫБРАННОГО МЕСЯЦА (решение пользователя 2026-09-11).
Ни уроков, ни оплат, датированных позже последнего дня месяца, в нём не
существует: если на 31 июля у ребёнка оставалось 2 оплаченных урока, отчёт за
июль показывает 2, даже когда сегодня их уже 0. Следствие, которое надо
помнить: оплату нередко вносят следующим месяцем, и оплаченный ею урок
остаётся в снимке долгом — на отчётную дату денег за него ещё не было.

Правила денег не дублируются: партии — apps/finances/lots.py::build_lots,
очередь — apps/finances/fifo.py::compute_fifo.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from apps.core.utils.dates import msk_month_range
from apps.core.utils.decimal import js_number, round_kopecks
from apps.finances.fifo import compute_fifo
from apps.finances.repository import _date_str, fifo_inputs
from apps.payments.models import Payment
from apps.students.models import Student

_ZERO = Decimal('0')


@dataclass
class StudentMonthRow:
    """Одна строка отчёта: ученик и одна его оплата (либо долг, либо пустая)."""

    student_id: int
    full_name: str
    platform_id: str | None
    # None — строка долга или пустая строка ученика без движения.
    payment_id: int | None
    paid_at: str | None
    unit_price: Decimal | None
    attended_lessons: int | float
    worked_off: Decimal
    paid_in_month: Decimal       # сумма оплаты, если она пришла в этом месяце
    refunded_in_month: Decimal   # деньги этой оплаты, возвращённые в этом месяце
    remaining_lessons: int | float
    remaining_value: Decimal
    debt: Decimal


def _payment_meta(month_start: str, month_end: str) -> dict[int, dict]:
    """
    Справочник оплат: дата, сумма с доплатами, цена урока и касса месяца.

    Доплата к абонементу (kind='surcharge') собственной оплатой не считается —
    она поднимает цену своего блока внутри родительской оплаты, поэтому её сумма
    прибавляется к родителю (как в реестре платежей). Из этого следует важное:
    доплата, пришедшая в выбранном месяце, — это деньги месяца, даже если сама
    оплата-родитель сделана давно. Такие деньги учитываются в 'cash_in_month'
    родителя, иначе касса месяца занижается на сумму доплат.

    Даты оплат НЕ ограничиваются концом месяца: FIFO гасит уроки июля партией,
    записанной в августе (оплату часто вносят позже), и без этих партий
    отработка месяца рассыпается на фантомный долг.
    """
    rows = list(
        Payment.objects
        .values('id', 'student_id', 'paid_at', 'total_amount', 'lessons_count',
                'kind', 'parent_payment_id')
    )
    surcharge_total: dict[int, Decimal] = {}     # все доплаты к оплате
    surcharge_in_month: dict[int, Decimal] = {}  # из них пришедшие в месяце
    for r in rows:
        if r['kind'] != 'surcharge' or r['parent_payment_id'] is None:
            continue
        parent = r['parent_payment_id']
        amount = Decimal(r['total_amount'])
        surcharge_total[parent] = surcharge_total.get(parent, _ZERO) + amount
        if month_start <= _date_str(r['paid_at']) <= month_end:
            surcharge_in_month[parent] = surcharge_in_month.get(parent, _ZERO) + amount

    meta: dict[int, dict] = {}
    for r in rows:
        if r['kind'] in ('surcharge', 'refund'):
            continue  # доплата живёт внутри родителя, возврат — не оплата
        lessons = int(r['lessons_count'] or 0)
        paid_at = _date_str(r['paid_at'])
        gross = Decimal(r['total_amount']) + surcharge_total.get(r['id'], _ZERO)
        own_in_month = Decimal(r['total_amount']) if month_start <= paid_at <= month_end else _ZERO
        meta[r['id']] = {
            'student_id': r['student_id'],
            'paid_at': paid_at,
            'unit_price': round_kopecks(gross / lessons) if lessons > 0 else _ZERO,
            # Деньги ЭТОГО месяца по этой оплате: сама оплата, если она месяца,
            # плюс её доплаты, пришедшие в месяце.
            'cash_in_month': own_in_month + surcharge_in_month.get(r['id'], _ZERO),
        }
    return meta


def _absorb_residual(rows: list['StudentMonthRow'], attr: str, total: Decimal) -> None:
    """Подогнать сумму поля `attr` по строкам ученика под его точный итог `total`.

    Каждая строка округлена до копеек отдельно, поэтому их сумма может отличаться
    от округлённого итога ученика на копейку. Разницу добавляем последней строке,
    где это поле ненулевое: тогда сумма колонки по всему листу равна итогу школы.
    """
    if not rows:
        return
    current = sum((getattr(r, attr) for r in rows), _ZERO)
    residual = total - current
    if residual == _ZERO:
        return
    for row in reversed(rows):
        if getattr(row, attr) != _ZERO:
            setattr(row, attr, getattr(row, attr) + residual)
            return
    setattr(rows[-1], attr, getattr(rows[-1], attr) + residual)


def collect_student_month(month: str) -> list[StudentMonthRow]:
    """
    Строки отчёта по ВСЕМ ученикам системы за указанный месяц.

    month: 'YYYY-MM'. Порядок: ученики по алфавиту, внутри ученика — оплаты по
    дате, последней идёт строка долга.

    Raises:
        ValueError: month не в формате YYYY-MM / невалидный месяц (1-12).
    """
    month_start, month_end = msk_month_range(f'{month}-01')
    # compute_fifo работает с эксклюзивной верхней границей [start, end).
    month_end_exclusive = (
        datetime.date.fromisoformat(month_end) + datetime.timedelta(days=1)
    ).strftime('%Y-%m-%d')

    students = list(
        Student.objects.order_by('full_name').values('id', 'full_name', 'platform_id')
    )
    meta = _payment_meta(month_start, month_end)

    inp = fifo_inputs()
    fifo_by_student: dict[int, dict] = {}
    for key in inp['keys']:
        # As-of: всё, что позже конца месяца, отчёт ещё «не видит» — и уроки,
        # и сами оплаты (иначе оплата следующего месяца раздувает аванс этого).
        # СНИМОК на конец месяца: ни уроков, ни оплат позже 23:59 последнего дня
        # для отчёта не существует. Оплата, внесённая уже следующим месяцем,
        # в снимок не входит — значит, оплаченный ею урок остаётся долгом на
        # отчётную дату. Это и есть «финансовые данные на момент месяца».
        cons = [c for c in inp['cons_by_key'].get(key, []) if c['date'] <= month_end]
        lots = [
            lot for lot in inp['lots_by_key'].get(key, [])
            if meta.get(lot.get('payment_id'), {}).get('paid_at', '') <= month_end
        ]
        fifo_by_student[int(key)] = compute_fifo(
            lots, cons, month_start, month_end_exclusive,
        )

    # Деньги месяца по оплате (сама оплата и/или её доплаты) — такая оплата даёт
    # строку даже без единого урока в месяце.
    paid_this_month = {pid for pid, m in meta.items() if m['cash_in_month'] > 0}

    rows: list[StudentMonthRow] = []
    for s in students:
        sid = s['id']
        fifo = fifo_by_student.get(sid) or {}
        worked_money = fifo.get('worked_off_by_month_payment', {})
        worked_lessons = fifo.get('worked_off_lessons_by_month_payment', {})
        remaining_money = fifo.get('remaining_by_payment', {})
        remaining_lessons = fifo.get('remaining_lessons_by_payment', {})
        refunded_money = fifo.get('refunded_by_month_payment', {})

        # Строку даёт оплата, если в месяце с неё списывали деньги, ИЛИ деньги по
        # ней пришли в месяце, ИЛИ на конец месяца по ней остался неотработанный
        # хвост, ИЛИ из неё в месяце вернули деньги. Хвост — ради снимка: остаток
        # обязан быть виден у каждого ребёнка, даже если в этом месяце он не
        # занимался и не платил. Возврат — ради сверки аванса: полный возврат
        # обнуляет хвост, и без своей строки сумма возврата пропала бы.
        involved = {
            pid for (ym, pid) in worked_money if ym == month
        } | {
            pid for (ym, pid) in refunded_money if ym == month
        } | {
            pid for pid in paid_this_month if meta[pid]['student_id'] == sid
        } | {
            pid for pid, lessons in remaining_lessons.items() if lessons > 0
        }

        student_rows: list[StudentMonthRow] = []
        payment_ids = sorted(involved, key=lambda p: (meta[p]['paid_at'], p))
        for pid in payment_ids:
            m = meta[pid]
            student_rows.append(StudentMonthRow(
                student_id=sid,
                full_name=s['full_name'],
                platform_id=s['platform_id'],
                payment_id=pid,
                paid_at=m['paid_at'],
                unit_price=m['unit_price'],
                attended_lessons=js_number(worked_lessons.get((month, pid), _ZERO)),
                worked_off=round_kopecks(worked_money.get((month, pid), _ZERO)),
                paid_in_month=m['cash_in_month'],
                refunded_in_month=round_kopecks(refunded_money.get((month, pid), _ZERO)),
                remaining_lessons=js_number(remaining_lessons.get(pid, _ZERO)),
                remaining_value=round_kopecks(remaining_money.get(pid, _ZERO)),
                debt=_ZERO,
            ))

        # Округление по строкам не обязано совпасть с округлением суммы ученика:
        # невязку (не больше копейки) отдаём последней строке с ненулевой
        # величиной, чтобы колонка отчёта сходилась с итогом школы за месяц.
        # Строки покрывают и все оплаты месяца, и весь непогашенный хвост,
        # поэтому обе колонки обязаны сойтись с итогом ученика до копейки.
        _absorb_residual(student_rows, 'worked_off', fifo.get('worked_off_month', _ZERO))
        _absorb_residual(student_rows, 'remaining_value', fifo.get('remaining_value', _ZERO))
        _absorb_residual(student_rows, 'refunded_in_month', round_kopecks(sum(
            (v for (ym, _), v in refunded_money.items() if ym == month), _ZERO)))

        # Уроки сверх оплаченных: своей оплаты у них нет — отдельная строка.
        # «Долг» — величина НА КОНЕЦ МЕСЯЦА (как и «Аванс»), а не только за месяц:
        # иначе накопленный долг исчезал бы из отчёта в тот же месяц, когда
        # ученик перестал добавлять новые уроки в минус.
        debt_value = fifo.get('over_consumed_value', _ZERO)
        debt_lessons = fifo.get('over_consumed_lessons', _ZERO)
        if debt_lessons > 0 and debt_value == _ZERO:
            # FIFO оценивает долг по цене последней партии, а её нет вовсе:
            # к отчётной дате ученик ещё ни разу не платил. Берём цену из
            # ближайшей его оплаты — единственный известный тариф этого ребёнка.
            nearest = min(
                (m for m in meta.values() if m['student_id'] == sid and m['unit_price'] > 0),
                key=lambda m: m['paid_at'], default=None,
            )
            if nearest is not None:
                debt_value = round_kopecks(nearest['unit_price'] * debt_lessons)
        if debt_lessons > 0:
            month_lessons = fifo.get('over_consumed_lessons_month', _ZERO)
            student_rows.append(StudentMonthRow(
                student_id=sid,
                full_name=s['full_name'],
                platform_id=s['platform_id'],
                payment_id=None,
                paid_at=None,
                # Своей цены у долга нет — берём цену последней оплаты ученика
                # (та же, по которой FIFO оценил долг деньгами).
                unit_price=round_kopecks(debt_value / debt_lessons),
                # Посещено — уроки в минус ИМЕННО этого месяца (может быть 0,
                # если долг перешёл с прошлых месяцев).
                attended_lessons=js_number(month_lessons),
                worked_off=_ZERO,
                paid_in_month=_ZERO,
                refunded_in_month=_ZERO,
                # Остаток оплаченных уроков у долга отрицательный: сумма колонки
                # по ученику даёт его настоящий баланс (аванс минус долг).
                remaining_lessons=js_number(-debt_lessons),
                remaining_value=_ZERO,
                debt=debt_value,
            ))

        if not student_rows:
            student_rows.append(StudentMonthRow(
                student_id=sid,
                full_name=s['full_name'],
                platform_id=s['platform_id'],
                payment_id=None,
                paid_at=None,
                unit_price=None,
                attended_lessons=0,
                worked_off=_ZERO,
                paid_in_month=_ZERO,
                refunded_in_month=_ZERO,
                remaining_lessons=0,
                remaining_value=_ZERO,
                debt=_ZERO,
            ))
        rows.extend(student_rows)
    return rows


_MONEY_FMT = '#,##0.00'
_DATE_FMT = 'DD.MM.YYYY'
_FONT_NAME = 'Calibri'
_FONT_SIZE = 11
# Оформление то же, что у реестра платежей: блоки шапки своим оттенком серого.
_HEADER_WHO = 'FF595959'     # кто и сколько отзанимался
_HEADER_PAY = 'FF7F7F7F'     # оплата, с которой списывались деньги
_HEADER_TOTAL = 'FF595959'   # остатки и долг
_GRID = 'FFD9D9D9'

HEADERS = [
    'ФИО ученика', 'Platform ID', 'Посещено уроков за месяц',
    'Отработано деньгами за месяц, ₽', 'Стоимость 1 урока, ₽', 'Дата оплаты',
    'Итого оплачено за месяц, ₽', 'Возврат за месяц, ₽', 'Остаток оплаченных уроков',
    'Остаток аванса, ₽', 'Долг, ₽',
]


def build_workbook(rows: list[StudentMonthRow]):
    """Собрать openpyxl.Workbook отчёта (строка = ученик × оплата), без сохранения."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Отчёт'
    ws.append(HEADERS)

    last_col = len(HEADERS)
    header_font = Font(name=_FONT_NAME, size=_FONT_SIZE, bold=True, color='FFFFFFFF')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for col in range(1, last_col + 1):
        if col <= 4:
            colour = _HEADER_WHO
        elif col <= 8:
            colour = _HEADER_PAY
        else:
            colour = _HEADER_TOTAL
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.alignment = header_align
        cell.fill = PatternFill('solid', start_color=colour, end_color=colour)
    ws.row_dimensions[1].height = 32

    for row in rows:
        ws.append([
            row.full_name,
            row.platform_id or '-',
            row.attended_lessons,
            float(row.worked_off),
            float(row.unit_price) if row.unit_price is not None else '-',
            datetime.date.fromisoformat(row.paid_at) if row.paid_at else '-',
            float(row.paid_in_month),
            float(row.refunded_in_month),
            row.remaining_lessons,
            float(row.remaining_value),
            float(row.debt),
        ])

    body_font = Font(name=_FONT_NAME, size=_FONT_SIZE)
    thin = Side(style='thin', color=_GRID)
    grid = Border(left=thin, right=thin, top=thin, bottom=thin)
    date_align = Alignment(horizontal='center')
    for excel_row in range(2, len(rows) + 2):
        for col in range(1, last_col + 1):
            cell = ws.cell(row=excel_row, column=col)
            cell.font = body_font
            cell.border = grid
        for col in (4, 5, 7, 8, 10, 11):
            ws.cell(row=excel_row, column=col).number_format = _MONEY_FMT
        date_cell = ws.cell(row=excel_row, column=6)
        if isinstance(date_cell.value, datetime.date):
            date_cell.number_format = _DATE_FMT
            date_cell.alignment = date_align

    widths = {1: 32, 2: 14, 3: 14, 4: 18, 5: 15, 6: 13, 7: 18, 8: 16, 9: 16, 10: 16, 11: 12}
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.auto_filter.ref = f'A1:{get_column_letter(last_col)}{len(rows) + 1}'
    # Закрепляем шапку вместе с ФИО: у ученика бывает несколько строк подряд.
    ws.freeze_panes = 'B2'
    return wb


def write_xlsx(rows: list[StudentMonthRow], path: str | Path) -> None:
    """Пишет отчёт в один лист «Отчёт» (файл на диск)."""
    wb = build_workbook(rows)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))


def render_bytes(rows: list[StudentMonthRow]) -> bytes:
    """Отчёт как xlsx-байты (для раздела «Отчёты»)."""
    import io
    wb = build_workbook(rows)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
