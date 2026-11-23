"""
Serializers for teachers.

TeacherReadSerializer  — вывод преподавателя.
TeacherWriteSerializer — ввод для POST (createTeacherSchema).
TeacherUpdateSerializer — ввод для PATCH (updateTeacherSchema, все поля необязательны).

Правила валидации — точный порт Zod-схем из shared/schemas.js:
  createTeacherSchema / updateTeacherSchema.
"""
from __future__ import annotations

from django.core.validators import URLValidator
from rest_framework import serializers

# Ссылки рендерятся как <a href>: только http(s), иначе в карточку можно
# положить что-то вроде ftp:// или вовсе не адрес.
_LINK_VALIDATORS = [URLValidator(schemes=['http', 'https'])]


def _link_field() -> serializers.CharField:
    return serializers.CharField(
        allow_null=True, allow_blank=True, required=False, max_length=500,
        validators=_LINK_VALIDATORS,
    )


class TeacherReadSerializer(serializers.Serializer):
    """
    Вывод преподавателя со всеми полями.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()
    email = serializers.CharField(allow_null=True)
    phone = serializers.CharField(allow_null=True)
    call_url = serializers.CharField(allow_null=True)
    vk_url = serializers.CharField(allow_null=True)
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
    call_url = _link_field()
    vk_url = _link_field()

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
    # В отличие от email/phone пустое значение СТИРАЕТ ссылку: звонки
    # пересоздают, страницы меняют — старую ссылку нужно уметь убрать.
    call_url = _link_field()
    vk_url = _link_field()
    active = serializers.BooleanField(required=False)

    def validate_name(self, value: str) -> str:
        return value.strip()

    def validate_email(self, value) -> str | None:
        if value in (None, ''):
            return value
        if '@' not in value or '.' not in value.split('@')[-1]:
            raise serializers.ValidationError('Enter a valid email address.')
        return value


class TeacherCommentSerializer(serializers.Serializer):
    """Read-only элемент списка комментариев (GET .../comments). Форма — как у StudentCommentSerializer."""

    id = serializers.IntegerField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()
    author_id = serializers.IntegerField(allow_null=True)
    author_name = serializers.SerializerMethodField()

    def get_author_name(self, obj) -> str | None:
        return obj.author.full_name if obj.author_id and obj.author else None


class TeacherCommentWriteSerializer(serializers.Serializer):
    """Ввод для POST .../comments."""

    body = serializers.CharField(max_length=5000, allow_blank=False)

    def validate_body(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('body must not be blank')
        return stripped
