"""
Serializers for directions.

DirectionReadSerializer   — вывод направления.
DirectionWriteSerializer  — ввод для POST (createDirectionSchema).
DirectionUpdateSerializer — ввод для PATCH (updateDirectionSchema, все поля необязательны).

Правила валидации — точный порт Zod-схем из shared/schemas.js:
  createDirectionSchema / updateDirectionSchema.

hexColor: '^#[0-9a-fA-F]{6}$'
"""
from __future__ import annotations

import re
from decimal import Decimal

from rest_framework import serializers

_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _validate_hex_color(value) -> str | None:
    """Проверяет формат #RRGGBB. Пустая строка и None допустимы."""
    if value in (None, ''):
        return value
    if not _HEX_COLOR_RE.match(str(value)):
        raise serializers.ValidationError('#RRGGBB format required.')
    return value


class DirectionReadSerializer(serializers.Serializer):
    """Вывод направления со всеми полями."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    total_lessons = serializers.IntegerField(allow_null=True)
    color = serializers.CharField(allow_null=True)
    subscription_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True
    )
    active = serializers.BooleanField()


class DirectionWriteSerializer(serializers.Serializer):
    """
    Вход для POST /api/admin/directions (createDirectionSchema).

    createDirectionSchema:
      name: z.string().trim().min(1)
      total_lessons: z.number().int().min(0).nullable().optional()
      color: hexColor.nullable().optional().or(z.literal(''))
      subscription_price: z.coerce.number().min(0).nullable().optional()
    """

    name = serializers.CharField(min_length=1)
    total_lessons = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    color = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    subscription_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0'),
        allow_null=True, required=False
    )

    def validate_name(self, value: str) -> str:
        return value.strip()

    def validate_color(self, value):
        return _validate_hex_color(value)


class DirectionUpdateSerializer(serializers.Serializer):
    """
    Вход для PATCH /api/admin/directions/:id (updateDirectionSchema).

    Все поля необязательны + active.
    """

    name = serializers.CharField(min_length=1, required=False)
    total_lessons = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    color = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    subscription_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0'),
        allow_null=True, required=False
    )
    active = serializers.BooleanField(required=False)

    def validate_name(self, value: str) -> str:
        return value.strip()

    def validate_color(self, value):
        return _validate_hex_color(value)
