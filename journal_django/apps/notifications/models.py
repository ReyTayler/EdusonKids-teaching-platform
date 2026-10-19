"""
Модели уведомлений.

TelegramUser       — справочник аккаунтов, которых бот когда-либо видел. Ещё не
                     привязка: нужен, чтобы админ выбирал из списка, а не набирал
                     ник руками, и чтобы взять числовой chat_id (по @нику Bot API
                     писать в личку не умеет).
TelegramRecipient  — собственно привязка «преподаватель ↔ аккаунт».
NotificationMessage— очередь исходящих, она же журнал доставки (transactional outbox).

См. docs/superpowers/specs/2026-08-03-telegram-notifications-design.md
"""
from __future__ import annotations

import pghistory
from django.db import models

from apps.notifications.constants import (
    CHANNEL_CHOICES, CHANNEL_EMAIL, KIND_CHOICES, STATUS_CHOICES, STATUS_QUEUED,
    TELEGRAM_CHANNELS,
)


class TelegramUser(models.Model):
    """Аккаунт Telegram, известный боту."""

    id = models.AutoField(primary_key=True)
    chat_id = models.BigIntegerField(unique=True)
    # @ник без собаки. В Telegram необязателен и может меняться — опираться на него
    # как на идентификатор нельзя, он только для глаз человека.
    username = models.TextField(null=True, blank=True)
    full_name = models.TextField()
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'telegram_users'
        indexes = [models.Index(fields=['username'], name='telegram_users_username_idx')]

    def __str__(self) -> str:
        return f'@{self.username}' if self.username else self.full_name


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class TelegramRecipient(models.Model):
    """Привязка преподавателя к аккаунту Telegram."""

    id = models.AutoField(primary_key=True)
    teacher = models.OneToOneField(
        'teachers.Teacher', on_delete=models.CASCADE,
        related_name='telegram_recipient',
    )
    telegram_user = models.ForeignKey(
        TelegramUser, on_delete=models.PROTECT, related_name='recipients',
    )
    # Снимается автоматически, когда Telegram отвечает «заблокирован» / «чат не найден».
    # Единственный внятный способ узнать, почему человек перестал получать сообщения.
    is_active = models.BooleanField(default=True)
    blocked_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'telegram_recipients'
        constraints = [
            # Один Telegram-аккаунт — не более чем у одного преподавателя.
            # Две привязки на один аккаунт всегда ошибка ввода: человек начал бы
            # получать чужие уведомления. Ограничение на уровне БД, а не только
            # пометкой в интерфейсе. Отдельным UniqueConstraint, а НЕ заменой FK
            # на OneToOneField: AlterField на FK умеет тихо затирать db-level
            # ON DELETE из прежних миграций (см. память проекта).
            models.UniqueConstraint(
                fields=['telegram_user'],
                name='telegram_recipients_user_key',
            ),
        ]


class NotificationMessage(models.Model):
    """
    Одна строка = одно сообщение одному адресату: в чат Telegram (chat_id) или
    на почту (channel='email', recipient_email + subject).

    Дубль в общий чат — просто вторая строка с другим chat_id, никакой особой
    логики. Под pghistory СОЗНАТЕЛЬНО не ставится: техническая таблица с высокой
    оборачиваемостью, журналировать её изменения — чистый шум.
    """

    id = models.AutoField(primary_key=True)
    kind = models.TextField()
    channel = models.TextField()
    # Адресат Telegram. У письма его нет — адресат в recipient_email; какое из
    # двух полей обязательно, решает CHECK по каналу (см. Meta).
    chat_id = models.BigIntegerField(null=True, blank=True)
    recipient_email = models.TextField(null=True, blank=True)
    subject = models.TextField(null=True, blank=True)
    # Для отображения в админке. Null для сообщений в общий чат.
    recipient_teacher = models.ForeignKey(
        'teachers.Teacher', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='notification_messages',
    )
    text = models.TextField()
    # Ключ идемпотентности: повторная постановка того же сообщения молча ничего
    # не делает (ON CONFLICT DO NOTHING). Защита на уровне БД, а не кода.
    dedup_key = models.TextField(unique=True)
    # След для разбора: 'absence_resolution' + 42.
    source_kind = models.TextField(null=True, blank=True)
    source_id = models.IntegerField(null=True, blank=True)
    status = models.TextField(default=STATUS_QUEUED)
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'notification_messages'
        indexes = [
            # Рабочий индекс диспетчера: частичный, только по строкам в очереди.
            models.Index(
                fields=['created_at'], name='notif_queued_idx',
                condition=models.Q(status=STATUS_QUEUED),
            ),
            # Под фильтры и сортировку раздела «Уведомления».
            models.Index(fields=['kind', '-created_at'], name='notif_kind_created_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                name='notification_messages_kind_check',
                condition=models.Q(kind__in=KIND_CHOICES),
            ),
            models.CheckConstraint(
                name='notification_messages_channel_check',
                condition=models.Q(channel__in=CHANNEL_CHOICES),
            ),
            models.CheckConstraint(
                name='notification_messages_status_check',
                condition=models.Q(status__in=STATUS_CHOICES),
            ),
            # Адресат соответствует каналу: Telegram без chat_id или письмо без
            # адреса диспетчер отправить не может — такая строка висела бы в
            # очереди до MAX_ATTEMPTS. Ловим на вставке, а не на отправке.
            models.CheckConstraint(
                name='notification_messages_target_check',
                condition=(
                    models.Q(channel__in=TELEGRAM_CHANNELS, chat_id__isnull=False)
                    | models.Q(channel=CHANNEL_EMAIL, recipient_email__isnull=False,
                               subject__isnull=False)
                ),
            ),
        ]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class NotificationSettings(models.Model):
    """
    Общешкольный выключатель рассылки. Ровно одна строка (singleton).

    Выключено = сообщения НЕ создаются вовсе: ни точечные, ни дайджесты. Это
    сознательный выбор — при включении ничего не хлынет пачкой устаревших
    сообщений. Обратная сторона: о событиях за время паузы преподаватели не
    узнают, поэтому кто и когда выключил рассылку, видно в журнале изменений.

    Хранится отдельной таблицей: флаг общий для всей школы.
    """

    id = models.AutoField(primary_key=True)
    is_enabled = models.BooleanField(default=True)
    # Email-напоминания родителям («урок завтра», «урок через час»). Отдельный
    # флаг и выключен по умолчанию: письма уходят клиентам школы, а не
    # сотрудникам, и включать их — решение админа (спека этапа 3, раздел D).
    # Работает только вместе с is_enabled: общий выключатель = полная тишина.
    parent_reminders_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    # Подпись «кто переключил» — простым текстом, без связи с учёткой.
    # Настоящий аудит и так ведёт журнал изменений (pghistory); жёсткий ключ
    # сюда только добавил бы хрупкости: учётку могут удалить, и строка настроек
    # не должна от этого страдать.
    updated_by = models.TextField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'notification_settings'

    @classmethod
    def load(cls) -> 'NotificationSettings':
        """Единственная строка; создаётся при первом обращении, рассылка включена."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj
