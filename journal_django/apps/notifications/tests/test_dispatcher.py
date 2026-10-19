"""Тесты диспетчера: успех, отказы, ретраи, подрезка хвоста."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.notifications import dispatcher
from apps.notifications.constants import (
    CHANNEL_DM, KIND_MAKEUP_ASSIGNED, MAX_ATTEMPTS,
    STATUS_FAILED, STATUS_QUEUED, STATUS_SENT,
)
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.notifications.telegram import SendResult
from apps.teachers.models import Teacher


@pytest.fixture
def linked(db):
    teacher = Teacher.objects.create(name='Анна', created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=555, username='anna', full_name='Анна')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
    return teacher


@pytest.fixture(autouse=True)
def no_pause():
    """
    В тестах пауза между отправками не нужна: она существует только для того,
    чтобы не упереться в лимит Telegram 30 сообщений/сек в проде. Без патча
    test_dispatch_is_batched (BATCH_SIZE+5 сообщений) занимал бы секунды.
    """
    with patch('apps.notifications.dispatcher.PAUSE_BETWEEN_SENDS', 0):
        yield


def _msg(dedup: str, teacher=None, chat_id: int = 555) -> NotificationMessage:
    return NotificationMessage.objects.create(
        kind=KIND_MAKEUP_ASSIGNED, channel=CHANNEL_DM, chat_id=chat_id,
        text='текст', dedup_key=dedup, status=STATUS_QUEUED,
        recipient_teacher=teacher,
    )


@pytest.mark.django_db
def test_successful_send_marks_sent(linked):
    row = _msg('a', linked)
    with patch('apps.notifications.dispatcher.telegram.send_message',
               return_value=SendResult(ok=True)):
        dispatcher.dispatch()
    row.refresh_from_db()
    assert row.status == STATUS_SENT
    assert row.sent_at is not None


@pytest.mark.django_db
def test_blocked_deactivates_recipient(linked):
    row = _msg('b', linked)
    with patch('apps.notifications.dispatcher.telegram.send_message',
               return_value=SendResult(ok=False, blocked=True,
                                       error='bot was blocked by the user')):
        dispatcher.dispatch()
    row.refresh_from_db()
    assert row.status == STATUS_FAILED
    recipient = TelegramRecipient.objects.get(teacher=linked)
    assert recipient.is_active is False
    assert 'blocked' in recipient.blocked_reason


@pytest.mark.django_db
def test_rate_limit_leaves_message_queued(linked):
    row = _msg('c', linked)
    with patch('apps.notifications.dispatcher.telegram.send_message',
               return_value=SendResult(ok=False, retry_after=5, error='Too Many Requests')):
        dispatcher.dispatch()
    row.refresh_from_db()
    assert row.status == STATUS_QUEUED
    assert row.attempts == 1


@pytest.mark.django_db
def test_temporary_error_fails_only_after_max_attempts(linked):
    row = _msg('d', linked)
    with patch('apps.notifications.dispatcher.telegram.send_message',
               return_value=SendResult(ok=False, error='timeout')):
        for _ in range(MAX_ATTEMPTS - 1):
            dispatcher.dispatch()
        row.refresh_from_db()
        assert row.status == STATUS_QUEUED

        dispatcher.dispatch()
    row.refresh_from_db()
    assert row.status == STATUS_FAILED
    assert row.attempts == MAX_ATTEMPTS
    assert 'timeout' in row.last_error


@pytest.mark.django_db
@override_settings(NOTIFICATIONS_HISTORY_LIMIT=3)
def test_trim_keeps_limit_and_never_touches_queued(linked):
    for i in range(6):
        m = _msg(f'sent-{i}', linked)
        m.status = STATUS_SENT
        m.save(update_fields=['status'])
    pending = _msg('still-queued', linked)

    dispatcher.trim_history()

    assert NotificationMessage.objects.filter(status=STATUS_SENT).count() == 3
    assert NotificationMessage.objects.filter(pk=pending.pk).exists()


@pytest.mark.django_db
def test_dispatch_is_batched(linked):
    for i in range(dispatcher.BATCH_SIZE + 5):
        _msg(f'batch-{i}', linked)
    with patch('apps.notifications.dispatcher.telegram.send_message',
               return_value=SendResult(ok=True)) as send:
        dispatcher.dispatch()
    assert send.call_count == dispatcher.BATCH_SIZE
