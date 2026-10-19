"""
Валидация входа раздела «База знаний».

Содержимое документа (content) здесь НЕ валидируется — этим занимается
content.validate_content, которому нужен список существующих картинок из БД.
Сериализатор проверяет только, что это объект.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.knowledge.models import READER_ROLES, KnowledgeDocument


class SectionWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, trim_whitespace=True, allow_blank=False)

    def validate_title(self, value: str) -> str:
        return value.strip()


class SectionUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, trim_whitespace=True, allow_blank=False)
    active = serializers.BooleanField(required=False)

    def validate_title(self, value: str) -> str:
        return value.strip()


class ReorderSerializer(serializers.Serializer):
    order = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class DocumentCreateSerializer(serializers.Serializer):
    section_id = serializers.IntegerField()
    title = serializers.CharField(max_length=300, trim_whitespace=True, allow_blank=False)

    def validate_title(self, value: str) -> str:
        return value.strip()


class DocumentUpdateSerializer(serializers.Serializer):
    """
    PATCH документа. Все поля необязательны — форма шлёт то, что изменилось.

    plain_text и published_at клиент не присылает: их пишет сервер.
    """

    title = serializers.CharField(max_length=300, required=False, trim_whitespace=True, allow_blank=False)
    section_id = serializers.IntegerField(required=False)
    content = serializers.JSONField(required=False)
    # Отметка времени версии, которую правит клиент. Присылает автосохранение,
    # чтобы сервер отказался затирать документ, изменённый в другом месте.
    base_updated_at = serializers.DateTimeField(required=False)
    reader_roles = serializers.ListField(
        child=serializers.ChoiceField(choices=READER_ROLES),
        required=False, allow_empty=True,
    )

    def validate_title(self, value: str) -> str:
        return value.strip()

    def validate_content(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('content должен быть объектом.')
        return value

    def validate_reader_roles(self, value):
        # Дубли безвредны для @>, но в БД им делать нечего.
        return sorted(set(value))


class DocumentReadSerializer(serializers.Serializer):
    """Форма ответа detail-эндпоинта. Используется для документирования формы."""

    id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    title = serializers.CharField()
    content = serializers.JSONField()
    status = serializers.ChoiceField(choices=KnowledgeDocument.Status.choices)
    reader_roles = serializers.ListField(child=serializers.CharField())
    position = serializers.IntegerField()
    published_at = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField()
