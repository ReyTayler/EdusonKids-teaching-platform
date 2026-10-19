"""Сериализаторы taskboard: только валидация ВХОДА. Чтение — dict из repository."""
from __future__ import annotations

from rest_framework import serializers

PRIORITIES = ['low', 'normal', 'high']
RESOLUTIONS = ['done', 'cancelled', 'irrelevant']


class TaskFilterSerializer(serializers.Serializer):
    """
    Валидация фильтров списка. Без неё мусор вроде ?board_id=abc долетает до
    ORM и падает ValueError мимо обработчика исключений — то есть 500 вместо 400.
    """
    board_id = serializers.IntegerField(required=False)
    stage_id = serializers.IntegerField(required=False)
    assignee_id = serializers.IntegerField(required=False)
    student_id = serializers.IntegerField(required=False)
    group_id = serializers.IntegerField(required=False)
    priority = serializers.ChoiceField(choices=PRIORITIES, required=False)
    only_open = serializers.BooleanField(required=False, default=False)
    overdue = serializers.BooleanField(required=False, default=False)
    due = serializers.ChoiceField(
        choices=['today', 'week', 'overdue', 'none'], required=False)
    q = serializers.CharField(required=False, allow_blank=True)


class TaskCreateSerializer(serializers.Serializer):
    board_id = serializers.IntegerField()
    title = serializers.CharField(max_length=500)
    # Без явной стадии сервис кладёт задачу в первую открытую стадию воронки.
    stage_id = serializers.IntegerField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    assignee_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    student_id = serializers.IntegerField(required=False, allow_null=True)
    group_id = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=PRIORITIES, required=False, default='normal')


class TaskPatchSerializer(serializers.Serializer):
    """Частичная правка. Стадии, результата и даты закрытия здесь нет намеренно —
    они меняются только через /move и /complete."""
    title = serializers.CharField(max_length=500, required=False)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    assignee_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    student_id = serializers.IntegerField(required=False, allow_null=True)
    group_id = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=PRIORITIES, required=False)


class MoveSerializer(serializers.Serializer):
    to_stage_id = serializers.IntegerField()
    # Обязателен при переходе в закрытую стадию — проверяет сервис, потому что
    # категория целевой стадии известна только там.
    resolution = serializers.ChoiceField(
        choices=RESOLUTIONS, required=False, allow_null=True)


class CompleteSerializer(serializers.Serializer):
    resolution = serializers.ChoiceField(choices=RESOLUTIONS, required=False, default='done')


class CommentSerializer(serializers.Serializer):
    body = serializers.CharField()


class BoardWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    sort_order = serializers.IntegerField(required=False)


class StageWriteSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=200)
    color = serializers.RegexField(r'^#[0-9a-fA-F]{6}$', required=False, allow_null=True)
    category = serializers.ChoiceField(choices=['open', 'closed'])


class StageReorderSerializer(serializers.Serializer):
    order = serializers.ListField(child=serializers.IntegerField())  # stage_id в новом порядке


class WeekQuerySerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()

    # Недельный вид отдаёт список без пагинации, поэтому диапазон ограничен.
    # 62 дня — это «два месяца», с запасом на любой разумный календарный экран.
    MAX_RANGE_DAYS = 62

    def validate(self, data: dict) -> dict:
        if data['date_from'] > data['date_to']:
            raise serializers.ValidationError({'date_to': 'Конец диапазона раньше начала'})
        if (data['date_to'] - data['date_from']).days > self.MAX_RANGE_DAYS:
            raise serializers.ValidationError(
                {'date_to': f'Диапазон шире {self.MAX_RANGE_DAYS} дней'})
        return data
