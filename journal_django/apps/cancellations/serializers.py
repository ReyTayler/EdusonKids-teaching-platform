"""
Валидация входа API справочника причин отмены.

StrictSerializer (как в плане группы): неизвестное поле — 400, а не молчаливый
пропуск. Поэтому PATCH с side тоже 400: сторона причины неизменяема — смена
стороны переписала бы смысл уже сделанных отмен с этой причиной.
"""
from __future__ import annotations

import unicodedata

from rest_framework import serializers

from apps.cancellations.models import CancellationSide, LessonCancellation
from apps.scheduling.serializers import StrictSerializer

# Потолок позиции: справочник — единицы строк, а значение вне int32 БД отвергла
# бы DataError'ом (500), не ошибкой валидации.
MAX_POSITION = 10000
INVISIBLE_NAME = 'Название не может состоять из невидимых символов.'


def drop_format_chars(value: str) -> str:
    """Без невидимых символов форматирования (категория Unicode Cf — ​, ﻿, ⁠…).
    Пробелы и переводы строк не трогает: многострочный текст остаётся собой."""
    return ''.join(ch for ch in value if unicodedata.category(ch) != 'Cf')


def normalize_reason_name(value: str) -> str:
    """Имя причины в каноническом виде: без невидимых символов форматирования
    и с одиночными пробелами.

    Иначе «Заболел  ребёнок» и «Заболел​ребёнок» обошли бы уникальность
    активных имён, хотя в форме неотличимы от уже существующей причины."""
    return ' '.join(drop_format_chars(value).split())


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


class GroupCancellationRowSerializer(serializers.ModelSerializer):
    """Строка вкладки группы «Отмены уроков» (read-only).

    compensated не отдаётся ни одной роли (В3): колонки компенсации во вкладке
    нет, а флаг решает о деньгах преподавателя. reason_text у «Другой причины» —
    полный текст: вкладку читают менеджеры, а не преподаватель."""

    # '%H:%M', как время урока в плане группы: секунды у слота всегда нулевые.
    scheduled_time = serializers.TimeField(format='%H:%M', read_only=True)
    teacher_name = serializers.CharField(source='teacher.name', read_only=True,
                                         allow_null=True, default=None)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LessonCancellation
        fields = [
            'id', 'lesson_date', 'scheduled_time', 'side', 'reason_text', 'is_other',
            'notified_on', 'teacher_id', 'teacher_name', 'created_at', 'created_by_name',
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj: LessonCancellation) -> str | None:
        # Учётку удалили → SET NULL → имени нет. Без full_name — email, как в
        # остальных местах, где показывается автор действия.
        author = obj.created_by
        if author is None:
            return None
        return author.full_name or author.email
