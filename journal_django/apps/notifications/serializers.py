"""Сериализаторы раздела «Уведомления»."""
from __future__ import annotations

from rest_framework import serializers

from apps.notifications.models import NotificationMessage, NotificationSettings


class NotificationMessageSerializer(serializers.ModelSerializer):
    """
    Строка журнала доставки. teacher_name — None для сообщений в общий чат и
    писем родителям. Адресат: chat_id у Telegram, recipient_email у письма.
    """

    teacher_name = serializers.CharField(
        source='recipient_teacher.name', read_only=True, default=None,
    )

    class Meta:
        model = NotificationMessage
        fields = [
            'id', 'kind', 'channel', 'chat_id', 'recipient_email', 'subject',
            'teacher_name', 'text',
            'status', 'attempts', 'last_error', 'created_at', 'sent_at',
            'source_kind', 'source_id',
        ]


class NotificationSettingsSerializer(serializers.ModelSerializer):
    """
    Выключатели рассылки. Запись — частичная: можно прислать любой из флагов.

    Флаги принимаются только настоящими true/false: строка «нет» или «0» из
    кривого клиента не должна молча включить письма родителям.
    """

    is_enabled = serializers.BooleanField(required=False)
    parent_reminders_enabled = serializers.BooleanField(required=False)

    class Meta:
        model = NotificationSettings
        fields = ['is_enabled', 'parent_reminders_enabled', 'updated_at', 'updated_by']
        read_only_fields = ['updated_at', 'updated_by']

    def to_internal_value(self, data):
        errors = {
            name: ['Должно быть true или false.']
            for name in ('is_enabled', 'parent_reminders_enabled')
            if name in data and not isinstance(data[name], bool)
        }
        if errors:
            raise serializers.ValidationError(errors)
        return super().to_internal_value(data)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                'Нужен хотя бы один флаг: is_enabled или parent_reminders_enabled.')
        return attrs
