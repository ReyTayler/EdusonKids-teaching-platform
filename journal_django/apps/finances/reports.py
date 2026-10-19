"""
Отчёт по поступлениям и выручке: реестр платежей + запись в Excel.

Строка = поступление клиента: дата оплаты, цена 1 урока по этому платежу,
признанная выручка по календарным месяцам до выбранного включительно, выручка
итого, возвращённые деньги и остаток аванса на конец выбранного месяца.

Правила денег не дублируются: партии строит apps/finances/lots.py::build_lots,
очередь — apps/finances/fifo.py::compute_fifo. Отчёт лишь читает разрезы по
платежу (worked_off_by_month_payment / remaining_by_payment /
refunded_by_payment), которые FIFO отдаёт точными Decimal.

As-of: потребления обрезаются по последний день выбранного месяца ДО вызова
compute_fifo — отсюда и аванс на конец месяца, и отсутствие месяцев позже
выбранного, без отдельной арифметики.

См. docs/superpowers/specs/2026-09-08-accounting-payment-ledger-design.md
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from django.db.models import F, Sum

from apps.core.utils.dates import month_label, msk_month_range, next_month
from apps.core.utils.decimal import round_kopecks
from apps.finances.fifo import compute_fifo
from apps.finances.repository import _date_str, fifo_inputs
from apps.payments.models import Payment
from apps.students.models import Student

NO_DIRECTION = 'Без направления'

_ZERO = Decimal('0')


@dataclass
class PaymentLedgerRow:
    """Одно поступление клиента и судьба его денег на конец выбранного месяца."""

    payment_id: int
    student_id: int
    full_name: str
    platform_id: str | None
    direction_name: str
    paid_at: str                  # 'YYYY-MM-DD'
    total_amount: Decimal         # сумма самой оплаты, без доплат
    surcharge_amount: Decimal     # доплаты к абонементам этой оплаты
    unit_price: Decimal           # (сумма + доплаты) / уроков оплаты
    # 'YYYY-MM' → признанная выручка месяца (только месяцы с признанием).
    revenue_by_month: dict[str, Decimal] = field(default_factory=dict)
    revenue_total: Decimal = _ZERO
    refunded: Decimal = _ZERO
    advance: Decimal = _ZERO


@dataclass
class LedgerReport:
    """Готовые данные листа: месяцы-колонки и строки-платежи."""

    month: str
    months: list[str]
    rows: list[PaymentLedgerRow]


def _months_range(first: str, last: str) -> list[str]:
    """Сплошная шкала календарных месяцев [first, last]."""
    out = [first]
    while out[-1] < last:
        out.append(next_month(out[-1]))
    return out


def _round_by_month(exact_by_month: dict[str, Decimal]) -> tuple[dict[str, Decimal], Decimal]:
    """
    Округлить месяцы до копеек, отдав невязку последнему месяцу с признанием.

    Тот же приём, что в apps/finances/revenue_forecast.py: сумма показанных
    ячеек в точности равна округлённой признанной выручке платежа.
    """
    total = round_kopecks(sum(exact_by_month.values(), _ZERO))
    ordered = sorted(exact_by_month)  # 'YYYY-MM' лексикографически = хронологически
    rounded: dict[str, Decimal] = {}
    accumulated = _ZERO
    for i, ym in enumerate(ordered):
        if i < len(ordered) - 1:
            cell = round_kopecks(exact_by_month[ym])
            accumulated += cell
        else:
            cell = total - accumulated
        rounded[ym] = cell
    return rounded, total


def _received_in_month(month_start: str, month_end: str) -> set[int]:
    """
    Платежи, деньги по которым пришли внутри месяца.

    purchase/extra отвечают за себя. Доплата к абонементу (surcharge) собственной
    строки не имеет никогда — поэтому пришедшая в месяце доплата втягивает в
    отчёт СВОЮ родительскую оплату: её деньги видны в колонке «Доплаты».
    refund не поступление, а возврат: строки не создаёт, он виден колонкой
    «Возвращено» у тех платежей, чьи партии погасил.
    """
    rows = (
        Payment.objects
        .filter(paid_at__gte=month_start, paid_at__lte=month_end,
                kind__in=['purchase', 'extra', 'surcharge'])
        .values_list('id', 'kind', 'parent_payment_id')
    )
    received: set[int] = set()
    for payment_id, kind, parent_id in rows:
        if kind == 'surcharge':
            if parent_id is not None:
                received.add(parent_id)
        else:
            received.add(payment_id)
    return received


def collect_monthly_report(month: str) -> LedgerReport:
    """
    Реестр платежей за указанный месяц: пришедших и/или признавших выручку.

    month: 'YYYY-MM'. В отчёт попадает платёж, у которого в этом месяце есть
    признанная выручка ЛИБО в этом месяце пришли деньги (решение пользователя
    2026-09-10: касса месяца должна быть видна целиком, даже когда уроков по
    оплате ещё не было — такая строка показывает нулевую выручку и полный аванс).
    Признание показывается по всем месяцам от самого раннего среди отобранных
    строк до указанного включительно; аванс — остаток на конец указанного месяца.

    Raises:
        ValueError: month не в формате YYYY-MM / невалидный месяц (1-12).
    """
    month_start, month_end = msk_month_range(f'{month}-01')
    # compute_fifo работает с эксклюзивной верхней границей [start, end) —
    # msk_month_range отдаёт ВКЛЮЧИТЕЛЬНЫЙ последний день, поэтому +1 день.
    month_end_exclusive = (
        datetime.date.fromisoformat(month_end) + datetime.timedelta(days=1)
    ).strftime('%Y-%m-%d')

    inp = fifo_inputs()
    worked: dict[tuple[str, int], Decimal] = {}
    remaining: dict[int, Decimal] = {}
    refunded: dict[int, Decimal] = {}
    for key in inp['keys']:
        # As-of: всё, что позже конца месяца, отчёт ещё «не видит».
        cons = [c for c in inp['cons_by_key'].get(key, []) if c['date'] <= month_end]
        fifo = compute_fifo(
            inp['lots_by_key'].get(key, []), cons, month_start, month_end_exclusive,
        )
        # Платёж принадлежит ровно одному ученику, поэтому ключи разных
        # учеников не пересекаются и update() ничего не затирает.
        worked.update(fifo['worked_off_by_month_payment'])
        remaining.update(fifo['remaining_by_payment'])
        refunded.update(fifo['refunded_by_payment'])

    recognized = {pid for (ym, pid), value in worked.items() if ym == month and value > 0}
    selected = recognized | _received_in_month(month_start, month_end)
    if not selected:
        return LedgerReport(month=month, months=[month], rows=[])

    exact_by_payment: dict[int, dict[str, Decimal]] = {}
    for (ym, pid), value in worked.items():
        if pid in selected and value > 0:
            exact_by_payment.setdefault(pid, {})[ym] = value

    # Строка без единого месяца признания (деньги пришли, уроков ещё не было)
    # шкалу не двигает; если таких строк весь отчёт — шкала это сам месяц.
    earliest = min(
        (ym for months in exact_by_payment.values() for ym in months),
        default=month,
    )

    payments = list(
        Payment.objects
        .filter(id__in=selected)
        .values('id', 'student_id', 'paid_at', 'total_amount', 'lessons_count',
                direction_name=F('direction__name'))
    )
    surcharge_rows = (
        Payment.objects
        .filter(kind='surcharge', parent_payment_id__in=selected)
        .values('parent_payment_id')
        .annotate(total=Sum('total_amount'))
    )
    surcharge_by_parent = {r['parent_payment_id']: r['total'] for r in surcharge_rows}
    students = {
        s['id']: s
        for s in Student.objects
        .filter(id__in={p['student_id'] for p in payments})
        .values('id', 'full_name', 'platform_id')
    }

    rows: list[PaymentLedgerRow] = []
    for p in payments:
        pid = p['id']
        # Пусто у платежа, который в отчёт попал приходом денег, а не признанием.
        by_month, revenue_total = _round_by_month(exact_by_payment.get(pid, {}))
        refund_amount = round_kopecks(refunded.get(pid, _ZERO))
        surcharge = Decimal(surcharge_by_parent.get(pid) or 0)
        gross = Decimal(p['total_amount']) + surcharge
        lessons = int(p['lessons_count'] or 0)
        student = students.get(p['student_id'], {})
        rows.append(PaymentLedgerRow(
            payment_id=pid,
            student_id=p['student_id'],
            full_name=student.get('full_name') or '',
            platform_id=student.get('platform_id'),
            direction_name=p['direction_name'] or NO_DIRECTION,
            paid_at=_date_str(p['paid_at']),
            total_amount=Decimal(p['total_amount']),
            surcharge_amount=surcharge,
            unit_price=round_kopecks(gross / lessons) if lessons > 0 else _ZERO,
            revenue_by_month=by_month,
            revenue_total=revenue_total,
            refunded=refund_amount,
            # Аванс абсорбирует невязку округления: строка сходится всегда. В
            # точной арифметике это и есть remaining_by_payment (см. §4.4 спеки).
            advance=gross - revenue_total - refund_amount,
        ))

    rows.sort(key=lambda r: (r.full_name, r.paid_at, r.payment_id))
    return LedgerReport(month=month, months=_months_range(earliest, month), rows=rows)


_MONEY_FMT = '#,##0.00'
_DATE_FMT = 'DD.MM.YYYY'

# Оформление листа (референс пользователя 2026-09-08). Шапка разбита на
# смысловые блоки, каждый со своим оттенком серого; текст белый жирный. Внутри
# блока оттенок один — глаз читает «кто заплатил / сколько / когда признали /
# итог» как четыре зоны, а не как полсотни одинаковых колонок.
_FONT_NAME = 'Calibri'
_FONT_SIZE = 11
_HEADER_REF = 'FF595959'      # опорные: ФИО, Platform ID, направление, дата
_HEADER_MONEY = 'FF7F7F7F'    # деньги платежа: сумма, доплаты, цена урока
_HEADER_MONTH = 'FF9C9C9C'    # колонки-месяцы признания выручки
_HEADER_TOTAL = 'FF595959'    # итоги: выручка, возвраты, аванс
_GRID = 'FFD9D9D9'


def build_report_workbook(report: LedgerReport):
    """Собрать openpyxl.Workbook реестра (одна строка = один платёж), без сохранения.

    Общее ядро для write_report_xlsx (файл на диск, CLI-команда) и
    render_report_bytes (байты для раздела «Отчёты»)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Реестр'

    headers = [
        'ФИО ученика', 'Platform ID', 'Направление оплаты', 'Дата оплаты',
        'Сумма платежа, ₽', 'Доплаты, ₽', 'Цена 1 урока, ₽',
    ]
    headers += [month_label(ym) for ym in report.months]
    headers += ['Выручка итого, ₽', 'Возвращено, ₽', 'Аванс, ₽']
    ws.append(headers)

    months_base = 8                                   # 1-я колонка-месяц
    total_col = months_base + len(report.months)
    refunded_col = total_col + 1
    advance_col = refunded_col + 1

    header_font = Font(name=_FONT_NAME, size=_FONT_SIZE, bold=True, color='FFFFFFFF')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for col in range(1, advance_col + 1):
        if col <= 4:
            colour = _HEADER_REF
        elif col <= 7:
            colour = _HEADER_MONEY
        elif col < total_col:
            colour = _HEADER_MONTH
        else:
            colour = _HEADER_TOTAL
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.alignment = header_align
        cell.fill = PatternFill('solid', start_color=colour, end_color=colour)
    ws.row_dimensions[1].height = 32

    for row in report.rows:
        values: list = [
            row.full_name, row.platform_id or '-', row.direction_name,
            datetime.date.fromisoformat(row.paid_at),
            float(row.total_amount), float(row.surcharge_amount), float(row.unit_price),
        ]
        # Месяц без признания — пустая ячейка (а не '-'): лист разреженный, а
        # SUM по колонке месяца должен читаться без правок.
        values += [
            float(row.revenue_by_month[ym]) if ym in row.revenue_by_month else None
            for ym in report.months
        ]
        values += [float(row.revenue_total), float(row.refunded), float(row.advance)]
        ws.append(values)

    money_cols = [5, 6, 7, total_col, refunded_col, advance_col]
    money_cols += list(range(months_base, months_base + len(report.months)))
    body_font = Font(name=_FONT_NAME, size=_FONT_SIZE)
    thin = Side(style='thin', color=_GRID)
    grid = Border(left=thin, right=thin, top=thin, bottom=thin)
    date_align = Alignment(horizontal='center')
    for excel_row in range(2, len(report.rows) + 2):
        for col in range(1, advance_col + 1):
            cell = ws.cell(row=excel_row, column=col)
            cell.font = body_font
            cell.border = grid
        ws.cell(row=excel_row, column=4).number_format = _DATE_FMT
        ws.cell(row=excel_row, column=4).alignment = date_align
        for col in money_cols:
            ws.cell(row=excel_row, column=col).number_format = _MONEY_FMT

    widths = {1: 32, 2: 14, 3: 22, 4: 13, 5: 16, 6: 12, 7: 15,
              total_col: 16, refunded_col: 14, advance_col: 14}
    for i in range(len(report.months)):
        widths[months_base + i] = 13
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    last_col = get_column_letter(advance_col)
    ws.auto_filter.ref = f'A1:{last_col}{len(report.rows) + 1}'
    # Месяцев бывает под полсотни: закрепляем шапку ВМЕСТЕ с опорными колонками
    # (кто, куда и когда заплатил), иначе при прокрутке вправо строка теряет имя.
    ws.freeze_panes = 'E2'
    return wb


def write_report_xlsx(report: LedgerReport, path: str | Path) -> None:
    """Пишет реестр в один лист «Реестр» (файл на диск)."""
    wb = build_report_workbook(report)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))


def render_report_bytes(report: LedgerReport) -> bytes:
    """Реестр как xlsx-байты (для раздела «Отчёты»)."""
    import io
    wb = build_report_workbook(report)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
