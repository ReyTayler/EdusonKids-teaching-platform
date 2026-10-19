"""
Построитель «Бухгалтерского отчёта» (по ученикам за месяц).

Тонкая обёртка над apps.finances.student_month: собираем строки по месяцу и
отдаём xlsx-байты. Правила half-lesson/FIFO не дублируются.
"""
from __future__ import annotations

from apps.finances.student_month import collect_student_month, render_bytes


def build(month: str) -> tuple[bytes, int, str]:
    """(xlsx-байты, число учеников, имя файла). month — 'YYYY-MM'."""
    rows = collect_student_month(month)  # ValueError при кривом месяце → services пометит failure
    content = render_bytes(rows)
    filename = f'accounting_students_{month}.xlsx'
    return content, len(rows), filename
