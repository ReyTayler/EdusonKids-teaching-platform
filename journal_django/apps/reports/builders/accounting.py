"""
Построитель «Отчёта по поступлениям и выручке» (реестр платежей с признанием
выручки по месяцам).

Тонкая обёртка над apps.finances.reports (та же логика, что у CLI-команды
export_accounting_report): собираем реестр по месяцу и отдаём xlsx-байты для
ReportJob. Правила half-lesson/FIFO не дублируются.
"""
from __future__ import annotations

from apps.finances.reports import collect_monthly_report, render_report_bytes


def build(month: str) -> tuple[bytes, int, str]:
    """(xlsx-байты, число строк-платежей, имя файла). month — 'YYYY-MM'."""
    report = collect_monthly_report(month)  # ValueError при кривом месяце → services пометит failure
    content = render_report_bytes(report)
    filename = f'accounting_{month}.xlsx'
    return content, len(report.rows), filename
