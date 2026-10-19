"""
Построитель «Прогноза отработки денег» для раздела «Отчёты».

Тонкая обёртка над apps.finances.revenue_forecast (та же логика, что у CLI-команды
export_revenue_forecast): нарезка FIFO-хвоста по 4 урока в месяц не дублируется.
"""
from __future__ import annotations

from apps.finances.revenue_forecast import collect_forecast, render_bytes


def build(month: str | None = None, full_history: bool = False) -> tuple[bytes, int, str]:
    """(xlsx-байты, число строк ученик×направление, имя файла).

    month — 'YYYY-MM', месяц НАЧАЛА раскладки (None → текущий МСК-месяц).
    full_history — развернуть на всю историю: прошлые месяцы фактом отработки.
    """
    forecast = collect_forecast(month, full_history=full_history)
    content = render_bytes(forecast)
    suffix = '_full' if full_history else ''
    filename = f'revenue_forecast_{forecast.start_month}{suffix}.xlsx'
    return content, len(forecast.rows), filename
