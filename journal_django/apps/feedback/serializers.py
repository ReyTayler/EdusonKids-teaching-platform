"""Сериализаторы раздела «Отзывы по урокам»."""
from __future__ import annotations

from rest_framework import serializers

from apps.feedback.models import LessonFeedback

# Предел длины комментария. В БД TextField без ограничения; предел на входе —
# защита от мусорного многомегабайтного тела, а не правило предметной области.
COMMENT_MAX_LENGTH = 5000

_SCORE_ERRORS = {
    'required': 'Поставьте оценку от 1 до 10.',
    'null': 'Поставьте оценку от 1 до 10.',
    'invalid': 'Оценка — целое число от 1 до 10.',
    'min_value': 'Оценка — целое число от 1 до 10.',
    'max_value': 'Оценка — целое число от 1 до 10.',
}


def _score_field() -> serializers.IntegerField:
    return serializers.IntegerField(min_value=1, max_value=10, error_messages=_SCORE_ERRORS)


class LessonFeedbackInputSerializer(serializers.Serializer):
    """Блок feedback в POST /api/submitLesson (запись урока преподавателем).

    Все поля обязательны; ссылки на запись тут нет — она поле урока (recordUrl) и
    необязательна. techProblems — без значения по умолчанию: «не ответил» и
    «проблем не было» — разные вещи, молча подставлять «Нет» нельзя.
    Имя techProblems — camelCase, как у соседних полей тела (recordUrl,
    plannedLessonId); в validated_data — tech_problems, имя поля модели.
    """

    understanding = _score_field()
    interest = _score_field()
    techProblems = serializers.BooleanField(
        source='tech_problems',
        error_messages={
            'required': 'Укажите, были ли технические проблемы.',
            'null': 'Укажите, были ли технические проблемы.',
            'invalid': 'Укажите, были ли технические проблемы.',
        },
    )
    # trim_whitespace (по умолчанию) + allow_blank=False: комментарий из одних
    # пробелов — пустой.
    comment = serializers.CharField(
        max_length=COMMENT_MAX_LENGTH,
        error_messages={
            'required': 'Напишите, как прошёл урок.',
            'null': 'Напишите, как прошёл урок.',
            'blank': 'Напишите, как прошёл урок.',
            'max_length': f'Комментарий длиннее {COMMENT_MAX_LENGTH} символов.',
        },
    )


class LessonFeedbackFilterSerializer(serializers.Serializer):
    """Фильтры списка из query-строки. Мусор (не число, ≤ 0, не дата) → 400, а не
    молчаливый игнор: иначе опечатка в фильтре вернула бы всю ленту под видом
    «отфильтровано»."""
    group_id = serializers.IntegerField(required=False, min_value=1)
    teacher_id = serializers.IntegerField(required=False, min_value=1)
    lesson_id = serializers.IntegerField(required=False, min_value=1)
    # Период — по дате урока (обе границы включительно), а не по времени отправки:
    # «отзывы за октябрь» — это уроки октября, даже если отзыв пришёл импортом позже.
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        date_from, date_to = attrs.get('date_from'), attrs.get('date_to')
        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError({'date_from': 'Начало периода позже его конца.'})
        return attrs


class LessonFeedbackSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)
    group_name = serializers.CharField(source='group.name', read_only=True)
    # Ссылка на запись — поле урока; у отзыва «не состоялся» урока нет → null.
    record_url = serializers.CharField(source='lesson.record_url', read_only=True,
                                       allow_null=True, default=None)

    class Meta:
        model = LessonFeedback
        fields = [
            'id', 'lesson_id', 'group_id', 'group_name', 'lesson_date', 'record_url',
            'teacher_id', 'teacher_name', 'took_place', 'understanding', 'interest',
            'tech_problems', 'comment', 'submitted_at',
        ]
        read_only_fields = fields
