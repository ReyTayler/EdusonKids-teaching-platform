"""
Serializers for audit.

AuditLogReadSerializer — вывод записи аудита (GET-only).

Поля: l.*, a.email AS account_email (из LEFT JOIN accounts).
"""
from __future__ import annotations

from rest_framework import serializers


class AuditLogReadSerializer(serializers.Serializer):
    """
    Вывод записи security_audit_log.

    Включает поля из таблицы + account_email из JOIN.
    """

    id = serializers.IntegerField()
    occurred_at = serializers.DateTimeField()
    account_id = serializers.IntegerField(allow_null=True)
    actor_email = serializers.CharField(allow_null=True)
    event = serializers.CharField()
    ip = serializers.CharField(allow_null=True)
    user_agent = serializers.CharField(allow_null=True)
    target_id = serializers.IntegerField(allow_null=True)
    meta = serializers.JSONField(allow_null=True)
    # Из JOIN
    account_email = serializers.CharField(allow_null=True, required=False)
