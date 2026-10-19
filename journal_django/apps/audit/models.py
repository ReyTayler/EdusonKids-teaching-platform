"""
Таблицы:
  security_audit_log — журнал событий безопасности (РСБ, ФСТЭК №21)
  sync_failures      — журнал сбоев синхронизации Sheets→PG (инфраструктура)

Схема: db/migrations/014_security_audit_log.sql + 001_initial_schema.sql.
"""
from __future__ import annotations

from django.db import models

from apps.core.db_fields import TolerantJSONField


class SecurityAuditLog(models.Model):
    """
    Запись журнала безопасности.

    Соответствует таблице `security_audit_log`.
    Только для чтения (GET-only endpoint).
    """

    id = models.BigAutoField(primary_key=True)
    occurred_at = models.DateTimeField()
    # FK → accounts(id), nullable. NO ACTION в БД → DO_NOTHING (managed=False).
    account = models.ForeignKey(
        'accounts.Account',
        on_delete=models.SET_NULL,
        db_column='account_id',
        related_name='audit_events',
        null=True,
        blank=True,
    )
    actor_email = models.TextField(null=True, blank=True)
    event = models.TextField()
    ip = models.TextField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    # target_id — полиморфная ссылка (в БД без REFERENCES), поэтому обычный int.
    target_id = models.IntegerField(null=True, blank=True)
    meta = TolerantJSONField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'security_audit_log'
        indexes = [
            models.Index(fields=['-occurred_at'], name='sec_audit_log_occurred_idx'),
            models.Index(fields=['account', '-occurred_at'], name='security_audit_log_account_idx'),
        ]


class SyncFailure(models.Model):
    """
    Запись о сбое синхронизации (Sheets→PG backfill).

    Соответствует таблице `sync_failures` (db/migrations/001_initial_schema.sql).
    Инфраструктурная таблица Node-инструментов; модель — для inspectdb-паритета.
    """

    id = models.BigAutoField(primary_key=True)
    occurred_at = models.DateTimeField()
    operation = models.TextField()
    payload = models.JSONField()
    error_message = models.TextField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'sync_failures'
