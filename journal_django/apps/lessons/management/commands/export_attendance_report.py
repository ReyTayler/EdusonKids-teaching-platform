"""
python manage.py export_attendance_report --month=2026-07 [--out=path.xlsx]

Отчёт по посещаемости за месяц: по каждому ученику базы (и по каждой его
группе) — даты уроков и статус «Был» / «Не был» / «Сгорел».

См. docs/superpowers/specs/2026-07-27-attendance-monthly-report-design.md
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.lessons.attendance_report import collect_month, write_xlsx


class Command(BaseCommand):
    help = 'Отчёт по посещаемости за месяц в Excel: был/не был + дата урока по каждому ученику.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--month', required=True, type=str,
            help='Месяц отчёта в формате YYYY-MM, например 2026-07',
        )
        parser.add_argument(
            '--out', type=str, default=None,
            help='Путь к выходному .xlsx (по умолчанию reports/attendance_report_<month>.xlsx)',
        )

    def handle(self, *args, **opts):
        month = opts['month']
        try:
            rows = collect_month(month)
        except ValueError:
            raise CommandError(f'Неверный формат месяца: "{month}". Ожидается YYYY-MM.')

        out = opts['out']
        out_path = (
            Path(out) if out
            else Path(settings.BASE_DIR) / 'reports' / f'attendance_report_{month}.xlsx'
        )

        write_xlsx(rows, out_path)

        students = len({r.student_id for r in rows})
        lessons = sum(len(r.cells) for r in rows)
        self.stdout.write(self.style.SUCCESS(
            f'Готово: {students} учеников, {len(rows)} строк (ученик + группа), '
            f'{lessons} уроков. Файл сохранён в {out_path}'
        ))
