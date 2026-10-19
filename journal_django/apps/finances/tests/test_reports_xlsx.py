"""
Тесты записи листа реестра (apps/finances/reports.py::write_report_xlsx).
Без БД — строки собираются вручную.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import openpyxl

from apps.finances.reports import LedgerReport, PaymentLedgerRow, write_report_xlsx


def _report():
    return LedgerReport(
        month='2026-07',
        months=['2026-05', '2026-06', '2026-07'],
        rows=[
            PaymentLedgerRow(
                payment_id=1, student_id=10, full_name='Аня А.', platform_id='PL-1',
                direction_name='Minecraft', paid_at='2026-05-01',
                total_amount=Decimal('2000'), surcharge_amount=Decimal('0'),
                unit_price=Decimal('500.00'),
                revenue_by_month={'2026-05': Decimal('500.00'), '2026-07': Decimal('500.00')},
                revenue_total=Decimal('1000.00'), refunded=Decimal('0.00'),
                advance=Decimal('1000.00'),
            ),
            PaymentLedgerRow(
                payment_id=2, student_id=11, full_name='Боря Б.', platform_id=None,
                direction_name='Без направления', paid_at='2026-06-15',
                total_amount=Decimal('2000'), surcharge_amount=Decimal('400'),
                unit_price=Decimal('600.00'),
                revenue_by_month={'2026-06': Decimal('600.00')},
                revenue_total=Decimal('600.00'), refunded=Decimal('1800.00'),
                advance=Decimal('0.00'),
            ),
        ],
    )


def test_header_lists_fixed_columns_then_months_then_totals(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    assert ws.title == 'Реестр'
    assert [c.value for c in ws[1]] == [
        'ФИО ученика', 'Platform ID', 'Направление оплаты', 'Дата оплаты',
        'Сумма платежа, ₽', 'Доплаты, ₽', 'Цена 1 урока, ₽',
        'Май 2026', 'Июнь 2026', 'Июль 2026',
        'Выручка итого, ₽', 'Возвращено, ₽', 'Аванс, ₽',
    ]


def test_row_values_and_empty_month_cells(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    first = [c.value for c in ws[2]]
    # openpyxl всегда читает дату обратно как datetime.datetime — формат ячеек
    # Excel не различает «дату» и «дату-время».
    assert first[:7] == [
        'Аня А.', 'PL-1', 'Minecraft', datetime.datetime(2026, 5, 1), 2000.0, 0.0, 500.0,
    ]
    assert first[7] == 500.0    # май
    assert first[8] is None     # июнь — признания не было
    assert first[9] == 500.0    # июль
    assert first[10:] == [1000.0, 0.0, 1000.0]

    second = [c.value for c in ws[3]]
    assert second[1] == '-'     # пустой platform_id
    assert second[5] == 400.0   # доплаты
    assert second[10:] == [600.0, 1800.0, 0.0]


def test_money_and_date_formats(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    assert ws.cell(row=2, column=4).number_format == 'DD.MM.YYYY'
    for col in (5, 6, 7, 8, 9, 10, 11, 12, 13):
        assert ws.cell(row=2, column=col).number_format == '#,##0.00'


def test_empty_report_renders_header_only(tmp_path):
    out = tmp_path / 'empty.xlsx'

    write_report_xlsx(LedgerReport(month='2026-07', months=['2026-07'], rows=[]), out)

    ws = openpyxl.load_workbook(out).active
    assert ws.max_row == 1
    assert [c.value for c in ws[1]][7] == 'Июль 2026'


# ---------------------------------------------------------------------------
# Оформление листа (референс пользователя 2026-09-08: серые заголовки блоками,
# белый жирный текст, тонкая сетка, закреплённые опорные колонки)
# ---------------------------------------------------------------------------

def _fill(cell) -> str:
    return cell.fill.start_color.rgb


def test_header_blocks_have_their_own_grey_shades(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    # 1-4 опорные (кто и когда заплатил), 5-7 деньги платежа, 8-10 месяцы,
    # 11-13 итоги. Соседние блоки не должны совпадать по цвету.
    assert _fill(ws.cell(row=1, column=1)) == _fill(ws.cell(row=1, column=4))
    assert _fill(ws.cell(row=1, column=5)) == _fill(ws.cell(row=1, column=7))
    assert _fill(ws.cell(row=1, column=8)) == _fill(ws.cell(row=1, column=10))
    assert _fill(ws.cell(row=1, column=11)) == _fill(ws.cell(row=1, column=13))
    assert _fill(ws.cell(row=1, column=1)) != _fill(ws.cell(row=1, column=5))
    assert _fill(ws.cell(row=1, column=5)) != _fill(ws.cell(row=1, column=8))
    assert _fill(ws.cell(row=1, column=8)) != _fill(ws.cell(row=1, column=11))


def test_header_text_is_white_bold_and_centered(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    for col in range(1, 14):
        cell = ws.cell(row=1, column=col)
        assert cell.font.bold is True
        assert cell.font.color.rgb == 'FFFFFFFF'
        assert cell.alignment.horizontal == 'center'
        assert cell.alignment.wrap_text is True


def test_single_font_family_across_sheet(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    fonts = {ws.cell(row=r, column=c).font.name
             for r in range(1, 4) for c in range(1, 14)}
    assert fonts == {'Calibri'}


def test_data_cells_have_thin_grid_borders(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    cell = ws.cell(row=2, column=2)
    assert cell.border.left.style == 'thin'
    assert cell.border.bottom.style == 'thin'


def test_reference_columns_stay_visible_and_header_filters(tmp_path):
    out = tmp_path / 'ledger.xlsx'

    write_report_xlsx(_report(), out)

    ws = openpyxl.load_workbook(out).active
    # Месяцев может быть под полсотни — опорные колонки закреплены вместе с шапкой.
    assert ws.freeze_panes == 'E2'
    assert ws.auto_filter.ref == 'A1:M3'
