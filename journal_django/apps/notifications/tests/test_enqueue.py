"""Тесты постановки в очередь: идемпотентность и выбор адресатов."""
from __future__ import annotations

import pytest
from django.test import override_settings

from apps.notifications import services
from apps.notifications.constants import (
    CHANNEL_DM, CHANNEL_GROUP, KIND_MAKEUP_ASSIGNED, STATUS_QUEUED,
)
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.teachers.models import Teacher


@pytest.fixture
def teacher(db):
    return Teacher.objects.create(name='Анна Петрова', created_at='2026-08-01T00:00:00Z')


@pytest.fixture
def linked_teacher(teacher):
    tg = TelegramUser.objects.create(chat_id=555, username='anna', full_name='Анна')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
    return teacher


@pytest.mark.django_db
def test_enqueue_is_idempotent(linked_teacher):
    for _ in range(3):
        services.enqueue(
            kind=KIND_MAKEUP_ASSIGNED, channel=CHANNEL_DM, chat_id=555,
            text='текст', dedup_key='makeup_assigned:42:dm',
        )
    assert NotificationMessage.objects.filter(dedup_key='makeup_assigned:42:dm').count() == 1


@pytest.mark.django_db
def test_enqueue_return_value_reflects_actual_insert():
    """enqueue обязан возвращать True ровно тогда, когда строка реально создана.

    Раньше на bulk_create(ignore_conflicts=True) это было не так: PostgreSQL
    не проставляет pk обратно в объект даже при успешной вставке, из-за чего
    возвращаемое значение всегда было бы False. Следующие задачи (диспетчер,
    счётчики поставленных сообщений) полагаются на честный булев результат.
    """
    first = services.enqueue(
        kind=KIND_MAKEUP_ASSIGNED, channel=CHANNEL_DM, chat_id=555,
        text='текст', dedup_key='makeup_assigned:99:dm',
    )
    second = services.enqueue(
        kind=KIND_MAKEUP_ASSIGNED, channel=CHANNEL_DM, chat_id=555,
        text='текст другой', dedup_key='makeup_assigned:99:dm',
    )
    assert first is True
    assert second is False


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_notify_teacher_creates_dm_and_group_rows(linked_teacher):
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED,
        teacher_id=linked_teacher.id,
        text='Доп.урок назначен.',
        dedup_prefix='makeup_assigned:42',
        source_kind='absence_resolution', source_id=42,
        also_to_group_chat=True,
    )
    rows = NotificationMessage.objects.order_by('channel')
    assert [r.channel for r in rows] == [CHANNEL_DM, CHANNEL_GROUP]
    assert {r.chat_id for r in rows} == {555, -100500}
    assert all(r.status == STATUS_QUEUED for r in rows)


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_unlinked_teacher_still_gets_group_chat_copy(teacher):
    """Привязки нет — личка не создаётся, но чат получает сообщение.

    Система деградирует мягко: молчать нельзя, иначе перенос потеряется.
    """
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=teacher.id, text='Доп.урок назначен.',
        dedup_prefix='makeup_assigned:43',
        source_kind='absence_resolution', source_id=43,
        also_to_group_chat=True,
    )
    rows = list(NotificationMessage.objects.all())
    assert len(rows) == 1
    assert rows[0].channel == CHANNEL_GROUP


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_inactive_recipient_gets_no_dm(linked_teacher):
    TelegramRecipient.objects.filter(teacher=linked_teacher).update(
        is_active=False, blocked_reason='bot was blocked by the user')
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=linked_teacher.id, text='т',
        dedup_prefix='makeup_assigned:44',
        source_kind='absence_resolution', source_id=44,
        also_to_group_chat=True,
    )
    assert not NotificationMessage.objects.filter(channel=CHANNEL_DM).exists()


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=0)
def test_group_chat_not_configured_is_not_an_error(linked_teacher):
    """Пустой TELEGRAM_GENERAL_CHAT_ID (локальная разработка) не должен ронять запись урока."""
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=linked_teacher.id, text='т',
        dedup_prefix='makeup_assigned:45',
        source_kind='absence_resolution', source_id=45,
        also_to_group_chat=True,
    )
    assert NotificationMessage.objects.filter(channel=CHANNEL_GROUP).count() == 0
    assert NotificationMessage.objects.filter(channel=CHANNEL_DM).count() == 1


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_group_copy_starts_with_mention(linked_teacher):
    """В общем чате адресат должен быть упомянут — иначе он пролистает
    сообщение про себя, и уведомления Telegram не даст."""
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=linked_teacher.id,
        text='Доп.урок назначен.', dedup_prefix='makeup_assigned:70',
        source_kind='absence_resolution', source_id=70,
        also_to_group_chat=True,
    )
    group = NotificationMessage.objects.get(channel=CHANNEL_GROUP)
    assert group.text.startswith('@anna')
    assert 'Доп.урок назначен.' in group.text


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_dm_has_no_mention(linked_teacher):
    """В личке упоминание избыточно — и так понятно, кому пишут."""
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=linked_teacher.id,
        text='Доп.урок назначен.', dedup_prefix='makeup_assigned:71',
        source_kind='absence_resolution', source_id=71,
        also_to_group_chat=True,
    )
    dm = NotificationMessage.objects.get(channel=CHANNEL_DM)
    assert dm.text == 'Доп.урок назначен.'


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_group_copy_names_unbound_teacher(teacher):
    """Привязки нет — пинга не будет, но имя адресата в чате обязано быть."""
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=teacher.id,
        text='Доп.урок назначен.', dedup_prefix='makeup_assigned:72',
        source_kind='absence_resolution', source_id=72,
        also_to_group_chat=True,
    )
    group = NotificationMessage.objects.get(channel=CHANNEL_GROUP)
    assert group.text.startswith('Анна Петрова')
