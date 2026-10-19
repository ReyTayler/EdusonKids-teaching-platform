"""Сериализаторы раздела «Уведомления»."""
from __future__ import annotations

from rest_framework import serializers

from apps.notifications.models import NotificationMessage


class NotificationMessageSerializer(serializers.ModelSerializer):
    """Строка журнала доставки. teacher_name — None для сообщений в общий чат."""

    teacher_name = serializers.CharField(
        source='recipient_teacher.name', read_only=True, default=None,
    )

    class Meta:
        model = NotificationMessage
        fields = [
            'id', 'kind', 'channel', 'chat_id', 'teacher_name', 'text',
            'status', 'attempts', 'last_error', 'created_at', 'sent_at',
            'source_kind', 'source_id',
        ]
