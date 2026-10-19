"""Сериализаторы раздела «Отзывы по урокам»."""
from __future__ import annotations

from rest_framework import serializers

from apps.feedback.models import LessonFeedback


class LessonFeedbackFilterSerializer(serializers.Serializer):
    """Фильтры списка из query-строки. Мусор (не число, ≤ 0) → 400, а не молчаливый
    игнор: иначе опечатка в фильтре вернула бы всю ленту под видом «отфильтровано»."""
    group_id = serializers.IntegerField(required=False, min_value=1)
    teacher_id = serializers.IntegerField(required=False, min_value=1)
    lesson_id = serializers.IntegerField(required=False, min_value=1)


class LessonFeedbackSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)

    class Meta:
        model = LessonFeedback
        fields = [
            'id', 'lesson_id', 'group_id', 'lesson_date',
            'teacher_id', 'teacher_name', 'took_place', 'understanding', 'interest',
            'tech_problems', 'comment', 'submitted_at',
        ]
        read_only_fields = fields
