"""
Serializers for payroll.

PayrollUpdateSerializer — вход для PATCH (updatePayrollSchema из shared/schemas.js):
все поля необязательны, total_students/present_count int≥0, payment/penalty number≥0.

PayrollReadSerializer — справочное представление вывода (views возвращают сырые
dict из repository, Decimal/date приводит DateSafeJSONRenderer).
"""
from __future__ import annotations

from rest_framework import serializers

from apps.core.fields import DateStringField


class PayrollUpdateSerializer(serializers.Serializer):
    """Вход для PATCH /api/admin/payroll/:id (updatePayrollSchema, все поля optional)."""

    # Допустимая разница с Express: DRF FloatField/IntegerField коэрсят числовые
    # строки ("700" → 700), Zod z.number() их отвергает (400). Реальный admin SPA
    # шлёт JSON-числа, поэтому e2e не страдает (как и в apps/lessons).
    total_students = serializers.IntegerField(min_value=0, required=False)
    present_count = serializers.IntegerField(min_value=0, required=False)
    payment = serializers.FloatField(min_value=0, required=False)
    penalty = serializers.FloatField(min_value=0, required=False)


class PayrollReadSerializer(serializers.Serializer):
    """Справочное представление строки payroll + контекст из JOIN (не применяется во views)."""

    id = serializers.IntegerField()
    lesson_id = serializers.IntegerField()
    teacher_id = serializers.IntegerField()
    total_students = serializers.IntegerField()
    present_count = serializers.IntegerField()
    payment = serializers.CharField()
    penalty = serializers.CharField()
    lesson_date = DateStringField(required=False)
    lesson_number = serializers.CharField(required=False)
    group_id = serializers.IntegerField(required=False)
    group_name = serializers.CharField(required=False)
    teacher_name = serializers.CharField(required=False)
