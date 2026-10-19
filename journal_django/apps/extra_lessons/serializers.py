"""
Сериализаторы-валидаторы extra_lessons. StrictSerializer — тот же паттерн,
что apps/scheduling/serializers.py (отклонять неизвестные поля).
"""
from __future__ import annotations

import re
from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import DateStringField
from apps.extra_lessons.models import VALID_DURATIONS

_TIME_RE = re.compile(r'^\d{2}:\d{2}(:\d{2})?$')


class StrictSerializer(serializers.Serializer):
    """Базовый сериализатор, отклоняющий неизвестные поля."""

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {k: 'Неизвестное поле.' for k in sorted(unknown)}
            )
        return attrs


class ExtraLessonCreateSerializer(StrictSerializer):
    """POST /api/admin/extra-lessons — назначить доп.урок."""

    missed_lesson_id = serializers.IntegerField(min_value=1)
    teacher_id = serializers.IntegerField(min_value=1)
    student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False,
    )
    scheduled_date = DateStringField()
    scheduled_time = serializers.CharField()
    duration_minutes = serializers.ChoiceField(choices=VALID_DURATIONS)

    def validate_scheduled_time(self, value):
        if not value or not _TIME_RE.match(value):
            raise serializers.ValidationError('Время должно быть в формате HH:MM или HH:MM:SS.')
        return value

    def validate_student_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('Ученики не должны повторяться.')
        return value


class ManualExtraLessonCreateSerializer(StrictSerializer):
    """POST /api/admin/extra-lessons/manual — назначить доп.урок СВЕРХ курса
    (kind='extra') вручную: без пропуска, с явной группой и опц. номером урока.

    lesson_number («за какой урок») необязателен — если не задан, при проведении
    берётся следующая позиция ученика в группе.
    """

    group_id = serializers.IntegerField(min_value=1)
    teacher_id = serializers.IntegerField(min_value=1)
    student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False,
    )
    scheduled_date = DateStringField()
    scheduled_time = serializers.CharField()
    duration_minutes = serializers.ChoiceField(choices=VALID_DURATIONS)
    lesson_number = serializers.DecimalField(
        max_digits=5, decimal_places=1, min_value=Decimal('0.5'),
        required=False, allow_null=True,
    )

    def validate_scheduled_time(self, value):
        if not value or not _TIME_RE.match(value):
            raise serializers.ValidationError('Время должно быть в формате HH:MM или HH:MM:SS.')
        return value

    def validate_student_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('Ученики не должны повторяться.')
        return value


class ExtraLessonRecordSerializer(StrictSerializer):
    """POST /api/extra-lessons/:id/record — фиксация проведения (teacher).

    Одна резолюция = один ученик, поэтому вместо списка attendance — единый
    флаг present (отметил учитель ученика на доп.уроке или нет).
    """

    record_url = serializers.CharField(
        allow_null=True, allow_blank=True, required=False, trim_whitespace=False,
    )
    present = serializers.BooleanField()
