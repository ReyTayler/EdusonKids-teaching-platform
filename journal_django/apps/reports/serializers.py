"""Сериализаторы параметров отчётов раздела «Отчёты»."""
from __future__ import annotations

from rest_framework import serializers

from apps.core.utils.dates import msk_now


class RenewalsReportParamsSerializer(serializers.Serializer):
    """Параметры «Отчёта по продлениям»: год и месяц."""
    year = serializers.IntegerField(min_value=2000, max_value=2100)
    month = serializers.IntegerField(min_value=1, max_value=12)

    def validate(self, attrs: dict) -> dict:
        # Месяц не может быть в будущем — отчёта по нему ещё нет. Дата — по МСК,
        # не по системным часам процесса (date.today() читает часовой пояс ОС,
        # который необязательно московский).
        today = msk_now().date()
        if (attrs['year'], attrs['month']) > (today.year, today.month):
            raise serializers.ValidationError({'month': 'Месяц ещё не наступил.'})
        return attrs


class _MonthStringSerializer(serializers.Serializer):
    """Общая база: месяц строкой YYYY-MM, не в будущем."""

    month = serializers.RegexField(
        r'^\d{4}-(0[1-9]|1[0-2])$', error_messages={'invalid': 'Ожидается месяц в формате YYYY-MM.'})

    def validate_month(self, value: str) -> str:
        y, m = (int(x) for x in value.split('-'))
        today = msk_now().date()
        if (y, m) > (today.year, today.month):
            raise serializers.ValidationError('Месяц ещё не наступил.')
        return value


class AccountingReportParamsSerializer(_MonthStringSerializer):
    """Параметры «Отчёта по поступлениям и выручке»: месяц строкой YYYY-MM (формат
    apps.finances.reports.collect_monthly_report)."""


class AttendanceReportParamsSerializer(_MonthStringSerializer):
    """Параметры «Отчёта по посещаемости»: месяц строкой YYYY-MM."""


class RevenueForecastParamsSerializer(_MonthStringSerializer):
    """Параметры «Прогноза отработки денег».

    month — месяц НАЧАЛА раскладки (обычно текущий; прошлый допустим — можно
    пересобрать прогноз «как он выглядел бы тогда»). full_history разворачивает
    строку на всю историю: прошлые месяцы фактом отработки, будущие прогнозом.
    """

    full_history = serializers.BooleanField(required=False, default=False)


class RetentionReportParamsSerializer(_MonthStringSerializer):
    """Параметры «Отчёта по переходимости»: месяц строкой YYYY-MM."""


class StudentsByTeacherParamsSerializer(_MonthStringSerializer):
    """Параметры отчёта «Ученики по преподавателям»: месяц строкой YYYY-MM."""


class AccountingStudentsParamsSerializer(_MonthStringSerializer):
    """Параметры «Бухгалтерского отчёта» (по ученикам): месяц строкой YYYY-MM."""


class PaidThroughParamsSerializer(serializers.Serializer):
    """Параметры отчёта «Оплачено до»: их нет — отчёт строится на сегодня (МСК)."""
