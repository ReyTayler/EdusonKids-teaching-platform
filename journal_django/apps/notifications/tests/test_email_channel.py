"""Канал email в общей очереди: схема, постановка, отправка диспетчером, подрезка."""
from __future__ import annotations

import datetime
import smtplib
from unittest.mock import patch

import pytest
from django.core import mail
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.utils import timezone

from apps.notifications import dispatcher, services
from apps.notifications.constants import (
    CHANNEL_DM, CHANNEL_EMAIL, KIND_LESSON_UNRECORDED, KIND_MAKEUP_ASSIGNED,
    MAX_ATTEMPTS, STATUS_FAILED, STATUS_QUEUED, STATUS_SENT,
)
from apps.notifications.models import (
    NotificationMessage, NotificationSettings, TelegramRecipient, TelegramUser,
)
from apps.notifications.serializers import NotificationMessageSerializer
from apps.teachers.models import Teacher

TO = 'teacher-one@example.test'


@pytest.fixture(autouse=True)
def no_pause():
    with patch('apps.notifications.dispatcher.PAUSE_BETWEEN_SENDS', 0):
        yield


@pytest.fixture
def linked(db):
    """Преподаватель с привязкой Telegram — чтобы проверить, что почта её не трогает."""
    teacher = Teacher.objects.create(name='Преподаватель Один', created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=555, username='one', full_name='Один')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
    return teacher


def _email(dedup: str = 'e1', teacher=None) -> NotificationMessage:
    services.enqueue_email(
        kind=KIND_LESSON_UNRECORDED, to=TO, subject='Тема письма', text='Текст письма',
        dedup_key=dedup, recipient_teacher_id=teacher.id if teacher else None)
    return NotificationMessage.objects.get(dedup_key=dedup)


# --- Схема (миграция 0004) ---------------------------------------------------

@pytest.mark.django_db
def test_migration_adds_columns_and_parent_flag_off_by_default():
    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'notification_messages' "
            "AND column_name IN ('chat_id', 'recipient_email', 'subject')")
        cols = dict(cur.fetchall())
        cur.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'notification_settings' "
            "AND column_name = 'parent_reminders_enabled'")
        assert cur.fetchone() is not None
    assert cols == {'chat_id': 'YES', 'recipient_email': 'YES', 'subject': 'YES'}
    NotificationSettings.objects.all().delete()
    assert NotificationSettings.load().parent_reminders_enabled is False


@pytest.mark.django_db
def test_email_row_without_address_is_rejected_by_db():
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationMessage.objects.create(
            kind=KIND_LESSON_UNRECORDED, channel=CHANNEL_EMAIL, subject='т', text='т',
            dedup_key='no-address')


@pytest.mark.django_db
def test_telegram_row_without_chat_id_is_still_rejected_by_db():
    """chat_id стал NULL-able ради писем — для Telegram он по-прежнему обязателен."""
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationMessage.objects.create(
            kind=KIND_MAKEUP_ASSIGNED, channel=CHANNEL_DM, text='т', dedup_key='no-chat')


@pytest.mark.django_db
def test_enqueue_email_is_idempotent_by_dedup_key():
    assert services.enqueue_email(kind=KIND_LESSON_UNRECORDED, to=TO, subject='s',
                                  text='t', dedup_key='same') is True
    assert services.enqueue_email(kind=KIND_LESSON_UNRECORDED, to=TO, subject='s',
                                  text='t', dedup_key='same') is False
    row = NotificationMessage.objects.get(dedup_key='same')
    assert (row.channel, row.chat_id, row.recipient_email) == (CHANNEL_EMAIL, None, TO)


@pytest.mark.django_db
def test_serializer_exposes_email_target():
    data = NotificationMessageSerializer(_email()).data
    assert data['channel'] == CHANNEL_EMAIL
    assert data['recipient_email'] == TO
    assert data['subject'] == 'Тема письма'
    assert data['chat_id'] is None


# --- Диспетчер -----------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DEFAULT_FROM_EMAIL='school@example.test')
def test_dispatch_sends_email_through_django_mail():
    row = _email()
    with patch('apps.notifications.dispatcher.telegram.send_message') as tg:
        assert dispatcher.dispatch() == 1
    tg.assert_not_called()

    assert len(mail.outbox) == 1
    letter = mail.outbox[0]
    assert letter.to == [TO]
    assert letter.subject == 'Тема письма'
    assert letter.body == 'Текст письма'
    assert letter.from_email == 'school@example.test'
    row.refresh_from_db()
    assert row.status == STATUS_SENT and row.sent_at is not None


@pytest.mark.django_db
def test_dispatch_is_silent_when_notifications_disabled():
    _email()
    row = NotificationSettings.load()
    row.is_enabled = False
    row.save(update_fields=['is_enabled'])
    assert dispatcher.dispatch() == 0
    assert mail.outbox == []


@pytest.mark.django_db
def test_refused_address_fails_at_once_and_keeps_telegram_binding(linked):
    row = _email(teacher=linked)
    refused = smtplib.SMTPRecipientsRefused({TO: (550, b'no such user')})
    with patch('apps.notifications.email_transport.send_mail', side_effect=refused):
        dispatcher.dispatch()
    row.refresh_from_db()
    assert row.status == STATUS_FAILED
    assert row.attempts == 1
    assert 'Адрес отклонён' in row.last_error
    # Отвергнутый почтовый адрес — не повод отключать человеку Telegram.
    assert TelegramRecipient.objects.get(teacher=linked).is_active is True


@pytest.mark.django_db
def test_transient_smtp_error_is_retried_until_max_attempts():
    row = _email()
    with patch('apps.notifications.email_transport.send_mail',
               side_effect=smtplib.SMTPServerDisconnected('connection lost')):
        dispatcher.dispatch()
        row.refresh_from_db()
        assert (row.status, row.attempts) == (STATUS_QUEUED, 1)
        for _ in range(MAX_ATTEMPTS - 1):
            dispatcher.dispatch()
    row.refresh_from_db()
    assert row.status == STATUS_FAILED
    assert row.attempts == MAX_ATTEMPTS
    assert 'SMTPServerDisconnected' in row.last_error


@pytest.mark.django_db
@override_settings(NOTIFICATIONS_HISTORY_LIMIT=1)
def test_trim_spares_fresh_emails_but_removes_old_ones():
    """Свежие письма держат dedup_key оконных напоминаний — подрезка их не трогает."""
    fresh = [_email(f'fresh-{i}') for i in range(3)]
    old = _email('old')
    NotificationMessage.objects.filter(dedup_key__startswith='fresh').update(status=STATUS_SENT)
    NotificationMessage.objects.filter(pk=old.pk).update(
        status=STATUS_SENT, created_at=timezone.now() - datetime.timedelta(days=2))
    # Telegram-строки подрезаются по-прежнему по лимиту.
    for i in range(3):
        NotificationMessage.objects.create(
            kind=KIND_MAKEUP_ASSIGNED, channel=CHANNEL_DM, chat_id=1, text='t',
            dedup_key=f'tg-{i}', status=STATUS_SENT)

    dispatcher.trim_history()

    assert NotificationMessage.objects.filter(pk__in=[r.pk for r in fresh]).count() == 3
    assert not NotificationMessage.objects.filter(pk=old.pk).exists()
    assert NotificationMessage.objects.filter(channel=CHANNEL_DM).count() == 1
