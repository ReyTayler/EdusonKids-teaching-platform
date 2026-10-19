"""
Сервисы раздела «Отчёты»: сборка отчёта по типу.

Отчёты НЕ хранятся в БД — build_report лишь строит байты xlsx, а перенос до
клиента берёт на себя celery result backend (см. tasks.generate_report_task).
Логика вынесена сюда, чтобы тестироваться без Celery/HTTP.
"""
from __future__ import annotations

from apps.reports.builders import (
    accounting, accounting_students, attendance, paid_through, renewals, retention,
    revenue_forecast, students_by_teacher,
)
from apps.reports.models import ReportType

# Диспетчер построителей по типу отчёта. Новый отчёт → новая запись здесь.
_BUILDERS = {
    ReportType.RENEWALS_MONTH: renewals.build,
    ReportType.ACCOUNTING_MONTH: accounting.build,
    ReportType.ATTENDANCE_MONTH: attendance.build,
    ReportType.REVENUE_FORECAST: revenue_forecast.build,
    ReportType.RETENTION: retention.build,
    ReportType.STUDENTS_BY_TEACHER: students_by_teacher.build,
    ReportType.ACCOUNTING_STUDENTS: accounting_students.build,
    ReportType.PAID_THROUGH: paid_through.build,
}


class UnknownReportType(ValueError):
    """Запрошен неизвестный тип отчёта."""


def build_report(report_type: str, params: dict) -> tuple[bytes, int, str]:
    """Собрать отчёт: (xlsx-байты, число строк, имя файла). Без сохранения."""
    builder = _BUILDERS.get(report_type)
    if builder is None:
        raise UnknownReportType(f'Неизвестный тип отчёта: {report_type}')
    return builder(**params)
