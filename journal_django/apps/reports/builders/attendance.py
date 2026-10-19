"""
Построитель «Отчёта по посещаемости за месяц» для раздела «Отчёты».

Тонкая обёртка над apps.lessons.attendance_report (та же логика, что у CLI-команды
export_attendance_report): правила статусов и схлопывание отработок не дублируются.
"""
from __future__ import annotations

from apps.lessons.attendance_report import collect_month, render_bytes


def build(month: str) -> tuple[bytes, int, str]:
    """(xlsx-байты, число строк ученик×группа, имя файла). month — 'YYYY-MM'."""
    rows = collect_month(month)  # ValueError при кривом месяце → services пометит failure
    content = render_bytes(rows)
    filename = f'attendance_{month}.xlsx'
    return content, len(rows), filename
