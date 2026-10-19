"""
Serializers for discounts.

DiscountReadSerializer   — вывод скидки.
DiscountWriteSerializer  — ввод для POST (createDiscountSchema).
DiscountUpdateSerializer — ввод для PATCH (updateDiscountSchema).

Правила валидации — точный порт Zod-схем из shared/schemas.js:
  createDiscountSchema:
    name:   z.string().trim().min(1)
    amount: z.coerce.number().min(0).max(1)
  updateDiscountSchema:
    name, amount — те же, все optional + active: z.boolean().optional()

amount — доля от 0 до 1 (numeric(5,4) в БД, не проценты).
"""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers


class DiscountReadSerializer(serializers.Serializer):
    """Вывод скидки со всеми полями."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    amount = serializers.DecimalField(max_digits=5, decimal_places=4)
    active = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class DiscountWriteSerializer(serializers.Serializer):
    """
    Вход для POST /api/admin/discounts (createDiscountSchema).

    amount: число от 0 до 1 включительно.
    """

    name = serializers.CharField(min_length=1)
    amount = serializers.DecimalField(
        max_digits=5, decimal_places=4,
        min_value=Decimal('0'), max_value=Decimal('1'),
    )

    def validate_name(self, value: str) -> str:
        return value.strip()


class DiscountUpdateSerializer(serializers.Serializer):
    """
    Вход для PATCH /api/admin/discounts/:id (updateDiscountSchema).

    Все поля необязательны + active.
    """

    name = serializers.CharField(min_length=1, required=False)
    amount = serializers.DecimalField(
        max_digits=5, decimal_places=4,
        min_value=Decimal('0'), max_value=Decimal('1'),
        required=False,
    )
    active = serializers.BooleanField(required=False)

    def validate_name(self, value: str) -> str:
        return value.strip()
