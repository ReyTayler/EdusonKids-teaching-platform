"""
Serializers for teachers.

TeacherReadSerializer  — вывод преподавателя.
TeacherWriteSerializer — ввод для POST (createTeacherSchema).
TeacherUpdateSerializer — ввод для PATCH (updateTeacherSchema, все поля необязательны).

Правила валидации — точный порт Zod-схем из shared/schemas.js:
  createTeacherSchema / updateTeacherSchema.
"""
from __future__ import annotations

from rest_framework import serializers


class TeacherReadSerializer(serializers.Serializer):
    """
    Вывод преподавателя со всеми полями.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()
    email = serializers.CharField(allow_null=True)
    phone = serializers.CharField(allow_null=True)
    active = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class TeacherWriteSerializer(serializers.Serializer):
    """
    Вход для POST /api/admin/teachers (createTeacherSchema).

    createTeacherSchema:
      name: z.string().trim().min(1)
      email: z.string().email().nullable().optional().or(z.literal(''))
      phone: z.string().nullable().optional()
    """

    name = serializers.CharField(min_length=1)
    email = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)

    def validate_name(self, value: str) -> str:
        return value.strip()

    def validate_email(self, value) -> str | None:
        if value in (None, ''):
            return value
        # базовая проверка формата email
        if '@' not in value or '.' not in value.split('@')[-1]:
            raise serializers.ValidationError('Enter a valid email address.')
        return value


class TeacherUpdateSerializer(serializers.Serializer):
    """
    Вход для PATCH /api/admin/teachers/:id (updateTeacherSchema).

    Все поля необязательны (partial).
    Дополнительно: active (boolean).
    """

    name = serializers.CharField(min_length=1, required=False)
    email = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    phone = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    active = serializers.BooleanField(required=False)

    def validate_name(self, value: str) -> str:
        return value.strip()

    def validate_email(self, value) -> str | None:
        if value in (None, ''):
            return value
        if '@' not in value or '.' not in value.split('@')[-1]:
            raise serializers.ValidationError('Enter a valid email address.')
        return value
