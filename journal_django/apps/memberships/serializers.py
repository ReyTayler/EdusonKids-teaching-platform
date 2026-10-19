"""
Serializers for memberships.

MembershipReadSerializer  — вывод membership со joined полями group_name, student_name.
MembershipWriteSerializer — ввод для POST (createMembershipSchema).
MembershipUpdateSerializer — ввод для PATCH (updateMembershipSchema).

Правила валидации — точный порт Zod-схем из shared/schemas.js:
  createMembershipSchema / updateMembershipSchema.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.core.fields import DateStringField


class MembershipReadSerializer(serializers.Serializer):
    """
    Вывод membership со всеми полями + joined group_name, student_name.

    Используется только для to_representation — group_name и student_name
    добавляются raw-SQL JOIN'ом в repository.
    """

    id = serializers.IntegerField()
    group_id = serializers.IntegerField()
    student_id = serializers.IntegerField()
    lessons_done = serializers.IntegerField()
    remaining = serializers.FloatField()
    start_date = DateStringField(allow_null=True)
    sheet_row = serializers.IntegerField(allow_null=True)
    active = serializers.BooleanField()
    # Поля из JOIN
    group_name = serializers.CharField(allow_null=True, required=False)
    student_name = serializers.CharField(allow_null=True, required=False)


class MembershipWriteSerializer(serializers.Serializer):
    """
    Вход для POST /api/admin/memberships (createMembershipSchema).

    Обязательные поля: group_id, student_id.
    Необязательные: lessons_done, start_date. remaining больше не принимается —
    вычисляется при чтении (apps.finances.balance_for_student).
    """

    group_id = serializers.IntegerField(min_value=1)
    student_id = serializers.IntegerField(min_value=1)
    lessons_done = serializers.FloatField(min_value=0, required=False)
    start_date = DateStringField(allow_null=True, required=False)


class MembershipUpdateSerializer(serializers.Serializer):
    """
    Вход для PATCH /api/admin/memberships/:id (updateMembershipSchema).

    Все поля необязательны. remaining не принимается — см. MembershipWriteSerializer.
    """

    lessons_done = serializers.FloatField(min_value=0, required=False)
    start_date = DateStringField(allow_null=True, required=False)
    active = serializers.BooleanField(required=False)


class MembershipTransferSerializer(serializers.Serializer):
    """Вход для POST /api/admin/memberships/:id/transfer."""

    to_group_id = serializers.IntegerField(min_value=1)


