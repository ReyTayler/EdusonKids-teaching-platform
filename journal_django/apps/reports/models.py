"""
Раздел «Отчёты» — БЕЗ персистентных моделей.

Сформированные отчёты НЕ хранятся на платформе: задача генерации уходит в Celery,
готовый xlsx переносится эфемерно через celery result backend (Redis в проде /
in-memory локально) и стримится клиенту сразу по готовности. В нашу БД ничего не
пишется — поэтому здесь только перечисление типов отчётов, без models.Model.
"""
from __future__ import annotations

from django.db import models


class ReportType(models.TextChoices):
    RENEWALS_MONTH = 'renewals_month', 'Отчёт по продлениям (за месяц)'
    ACCOUNTING_MONTH = 'accounting_month', 'Отчёт по поступлениям и выручке (за месяц)'
    ATTENDANCE_MONTH = 'attendance_month', 'Отчёт по посещаемости (за месяц)'
    REVENUE_FORECAST = 'revenue_forecast', 'Прогноз отработки денег по месяцам'
    RETENTION = 'retention', 'Отчёт по переходимости (за месяц)'
    STUDENTS_BY_TEACHER = 'students_by_teacher', 'Ученики по преподавателям (за месяц)'
    ACCOUNTING_STUDENTS = 'accounting_students', 'Бухгалтерский отчёт (за месяц)'
    PAID_THROUGH = 'paid_through', 'Оплачено до (на сегодня)'
