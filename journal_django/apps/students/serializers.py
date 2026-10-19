"""
Serializers for students.

StudentReadSerializer  — полный вывод ученика.
StudentWriteSerializer — ввод для POST (createStudentSchema).
StudentUpdateSerializer — ввод для PATCH (updateStudentSchema, все поля optional).

Правила валидации — точный порт Zod-схем из shared/schemas.js:
  createStudentSchema / updateStudentSchema.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.core.fields import DateStringField


class StudentReadSerializer(serializers.Serializer):
    """
    Полный вывод ученика.

    Используется для to_representation — поля совпадают с таблицей students.
    """

    id = serializers.IntegerField()
    full_name = serializers.CharField()
    birth_date = DateStringField(allow_null=True)
    platform_id = serializers.CharField(allow_null=True, allow_blank=True)
    crm_link = serializers.CharField(allow_null=True, allow_blank=True)
    # Контакты самого ребёнка (Eduson шлёт напоминания и ему).
    phone = serializers.CharField(allow_null=True, allow_blank=True)
    telegram = serializers.CharField(allow_null=True, allow_blank=True)
    email = serializers.CharField(allow_null=True, allow_blank=True)
    parent1_name = serializers.CharField(allow_null=True, allow_blank=True)
    parent1_phone = serializers.CharField(allow_null=True, allow_blank=True)
    parent1_email = serializers.CharField(allow_null=True, allow_blank=True)
    parent1_telegram = serializers.CharField(allow_null=True, allow_blank=True)
    parent2_name = serializers.CharField(allow_null=True, allow_blank=True)
    parent2_phone = serializers.CharField(allow_null=True, allow_blank=True)
    parent2_email = serializers.CharField(allow_null=True, allow_blank=True)
    manager_id = serializers.IntegerField(allow_null=True)
    manager_name = serializers.CharField(allow_null=True, allow_blank=True)
    # Стадия последней сделки продления — заменила enrollment_status.
    # dict или None; поля совпадают с renewal_stage (id/key/label/kind/sort_order).
    stage = serializers.DictField(allow_null=True)
    stage_is_open = serializers.BooleanField()
    stage_frozen_until_month = DateStringField(allow_null=True)
    created_at = serializers.DateTimeField()


class StudentWriteSerializer(serializers.Serializer):
    """
    Ввод для POST /api/admin/students (createStudentSchema).

    Обязательные поля: full_name.
    """

    full_name = serializers.CharField(min_length=1)
    birth_date = DateStringField(allow_null=True, required=False)
    platform_id = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    crm_link = serializers.URLField(allow_null=True, allow_blank=True, required=False)
    phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    telegram = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)
    parent1_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent1_phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent1_email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)
    parent1_telegram = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent2_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent2_phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent2_email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)

    def validate_full_name(self, value: str) -> str:
        return value.strip()


class StudentUpdateSerializer(serializers.Serializer):
    """
    Ввод для PATCH /api/admin/students/:id (updateStudentSchema).

    Все поля необязательны (partial по Zod .partial()).
    """

    full_name = serializers.CharField(min_length=1, required=False)
    birth_date = DateStringField(allow_null=True, required=False)
    platform_id = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    crm_link = serializers.URLField(allow_null=True, allow_blank=True, required=False)
    phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    telegram = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)
    parent1_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent1_phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent1_email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)
    parent1_telegram = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent2_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent2_phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    parent2_email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)

    def validate_full_name(self, value: str) -> str:
        return value.strip()


class StudentCommentSerializer(serializers.Serializer):
    """Read-only элемент списка комментариев (GET .../comments)."""

    id = serializers.IntegerField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()
    author_id = serializers.IntegerField(allow_null=True)
    author_name = serializers.SerializerMethodField()

    def get_author_name(self, obj) -> str | None:
        return obj.author.full_name if obj.author_id and obj.author else None


class StudentCommentWriteSerializer(serializers.Serializer):
    """Ввод для POST .../comments."""

    body = serializers.CharField(max_length=5000, allow_blank=False)

    def validate_body(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('body must not be blank')
        return stripped


class StudentManagerSerializer(serializers.Serializer):
    """Ввод PATCH /students/:id/manager. null — снять ответственного."""
    manager_id = serializers.IntegerField(allow_null=True)
