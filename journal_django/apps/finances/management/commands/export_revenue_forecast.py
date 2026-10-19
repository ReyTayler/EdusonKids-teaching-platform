"""
python manage.py export_revenue_forecast [--month=2026-07] [--out=path.xlsx]

Прогноз отработки внесённых денег: неотработанный остаток каждого ученика
разложен на календарные месяцы вперёд по 4 урока (1 абонемент) в месяц.
Лист «Сводка» + лист на каждое направление.

--month задаёт месяц, С КОТОРОГО начинается раскладка (по умолчанию — текущий
МСК-месяц, «на момент формирования отчёта»).

См. docs/superpowers/specs/2026-07-27-revenue-forecast-design.md
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.finances.revenue_forecast import collect_forecast, write_xlsx


class Command(BaseCommand):
    help = 'Прогноз отработки внесённых денег по месяцам вперёд (Excel).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--month', type=str, default=None,
            help='Месяц начала раскладки в формате YYYY-MM (по умолчанию текущий)',
        )
        parser.add_argument(
            '--full-history', action='store_true',
            help='Развернуть на всю историю: прошлые месяцы фактом отработки, '
                 'будущие — прогнозом (иначе только будущее)',
        )
        parser.add_argument(
            '--out', type=str, default=None,
            help='Путь к выходному .xlsx (по умолчанию reports/revenue_forecast_<month>.xlsx)',
        )

    def handle(self, *args, **opts):
        month = opts['month']
        full_history = opts['full_history']
        try:
            forecast = collect_forecast(month, full_history=full_history)
        except ValueError:
            raise CommandError(f'Неверный формат месяца: "{month}". Ожидается YYYY-MM.')

        out = opts['out']
        suffix = '_full' if full_history else ''
        out_path = (
            Path(out) if out
            else Path(settings.BASE_DIR) / 'reports'
            / f'revenue_forecast_{forecast.start_month}{suffix}.xlsx'
        )
        write_xlsx(forecast, out_path)

        students = len({r.student_id for r in forecast.rows})
        total = sum((r.remaining_value for r in forecast.rows), start=0)
        recognised = sum((r.worked_off_value for r in forecast.rows), start=0)
        tail = f', уже признано {recognised:.2f} руб.' if full_history else ''
        self.stdout.write(self.style.SUCCESS(
            f'Готово: {students} учеников, {len(forecast.rows)} строк, '
            f'{len(forecast.months)} месяцев (прогноз с {forecast.start_month}), '
            f'к отработке {total:.2f} руб.{tail} Файл сохранён в {out_path}'
        ))
