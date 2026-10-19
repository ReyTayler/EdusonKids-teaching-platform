"""
python manage.py export_accounting_report --month=2026-07 [--out=path.xlsx]

Реестр признания выручки по платежам за месяц: дата оплаты, цена 1 урока,
признанная выручка по месяцам, выручка итого, возвраты и остаток аванса.

См. docs/superpowers/specs/2026-09-08-accounting-payment-ledger-design.md
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.finances.reports import collect_monthly_report, write_report_xlsx


class Command(BaseCommand):
    help = 'Реестр признания выручки по платежам за месяц в Excel.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--month', required=True, type=str,
            help='Месяц отчёта в формате YYYY-MM, например 2026-07',
        )
        parser.add_argument(
            '--out', type=str, default=None,
            help='Путь к выходному .xlsx (по умолчанию reports/accounting_report_<month>.xlsx)',
        )

    def handle(self, *args, **opts):
        month = opts['month']
        try:
            report = collect_monthly_report(month)
        except ValueError:
            raise CommandError(f'Неверный формат месяца: "{month}". Ожидается YYYY-MM.')

        out = opts['out']
        out_path = Path(out) if out else Path(settings.BASE_DIR) / 'reports' / f'accounting_report_{month}.xlsx'

        write_report_xlsx(report, out_path)

        self.stdout.write(self.style.SUCCESS(
            f'Готово: {len(report.rows)} платежей, файл сохранён в {out_path}'
        ))
