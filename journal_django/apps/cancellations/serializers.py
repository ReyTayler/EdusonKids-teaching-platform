"""
Валидация входа API справочника причин отмены.

StrictSerializer (как в плане группы): неизвестное поле — 400, а не молчаливый
пропуск. Поэтому PATCH с side тоже 400: сторона причины неизменяема — смена
стороны переписала бы смысл уже сделанных отмен с этой причиной.
"""
from __future__ import annotations

import unicodedata

from rest_framework import serializers

from apps.cancellations.models import CancellationSide
from apps.scheduling.serializers import StrictSerializer

# Потолок позиции: справочник — единицы строк, а значение вне int32 БД отвергла
# бы DataError'ом (500), не ошибкой валидации.
MAX_POSITION = 10000
INVISIBLE_NAME = 'Название не может состоять из невидимых символов.'


def normalize_reason_name(value: str) -> str:
    """Имя причины в каноническом виде: без невидимых символов форматирования
    (категория Unicode Cf — ​, ﻿, ⁠…) и с одиночными пробелами.

    Иначе «Заболел  ребёнок» и «Заболел​ребёнок» обошли бы уникальность
    активных имён, хотя в форме неотличимы от уже существующей причины."""
    visible = ''.join(ch for ch in value if unicodedata.category(ch) != 'Cf')
    return ' '.join(visible.split())


def _name_field(**kwargs) -> serializers.CharField:
    # trim_whitespace=True: имя из одних пробелов становится пустым и
    # отклоняется allow_blank=False; невидимые символы добивает validate_name.
    return serializers.CharField(max_length=200, trim_whitespace=True, allow_blank=False,
                                 **kwargs)


def _validate_name(value: str) -> str:
    normalized = normalize_reason_name(value)
    if not normalized:
        raise serializers.ValidationError(INVISIBLE_NAME)
    return normalized


class ReasonCreateSerializer(StrictSerializer):
    side = serializers.ChoiceField(choices=CancellationSide.choices)
    name = _name_field()
    compensation = serializers.BooleanField(required=False, default=False)

    def validate_name(self, value: str) -> str:
        return _validate_name(value)


class ReasonUpdateSerializer(StrictSerializer):
    name = _name_field(required=False)
    compensation = serializers.BooleanField(required=False)
    position = serializers.IntegerField(required=False, min_value=0, max_value=MAX_POSITION)

    def validate_name(self, value: str) -> str:
        return _validate_name(value)
