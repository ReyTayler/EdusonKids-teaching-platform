"""Общешкольный выключатель рассылки: права, семантика «полной тишины», охват."""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.notifications import digests, dispatcher, services
from apps.notifications.constants import (
    KIND_MAKEUP_ASSIGNED, KIND_MORNING_DIGEST, STATUS_QUEUED,
)
from apps.notifications.models import (
    NotificationMessage, NotificationSettings, TelegramRecipient, TelegramUser,
)
from apps.notifications.telegram import SendResult
from apps.teachers.models import Teacher

URL = '/api/admin/notifications/toggle'
TODAY = datetime.date(2026, 8, 3)


@pytest.fixture
def linked(db):
    teacher = Teacher.objects.create(name='Анна Петрова', created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=555, username='anna', full_name='Анна')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
    return teacher


def _disable():
    row = NotificationSettings.load()
    row.is_enabled = False
    row.save(update_fields=['is_enabled'])


# --- Права -----------------------------------------------------------------

@pytest.mark.django_db
def test_manager_cannot_see_or_change(manager_client):
    assert manager_client.get(URL).status_code == 403
    assert manager_client.post(URL, {'is_enabled': False}, format='json').status_code == 403


@pytest.mark.django_db
def test_anonymous_is_denied(anon_client):
    assert anon_client.get(URL).status_code in (401, 403)


@pytest.mark.django_db
def test_enabled_by_default(admin_client):
    """Свежая установка не должна молчать: рассылка включена, пока не выключили."""
    response = admin_client.get(URL)
    assert response.status_code == 200
    assert response.data['is_enabled'] is True


@pytest.mark.django_db
def test_admin_toggles_and_state_persists(admin_client):
    off = admin_client.post(URL, {'is_enabled': False}, format='json')
    assert off.status_code == 200
    assert off.data['is_enabled'] is False
    assert admin_client.get(URL).data['is_enabled'] is False
    assert off.data['updated_by']          # видно, кто выключил

    on = admin_client.post(URL, {'is_enabled': True}, format='json')
    assert on.data['is_enabled'] is True


@pytest.mark.django_db
def test_rejects_non_boolean(admin_client):
    assert admin_client.post(URL, {'is_enabled': 'нет'}, format='json').status_code == 400


# --- Семантика: полная тишина ----------------------------------------------

@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_disabled_creates_no_pointwise_message(linked):
    """Выключено — сообщение не создаётся вовсе, ни в личку, ни в чат."""
    _disable()
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=linked.id, text='Доп.урок назначен.',
        dedup_prefix='makeup_assigned:80',
        source_kind='absence_resolution', source_id=80,
        also_to_group_chat=True,
    )
    assert NotificationMessage.objects.count() == 0


@pytest.mark.django_db
def test_disabled_stops_digests(linked):
    _disable()
    day_map = {linked.id: [
        {'time': '12:00', 'group': 'СИ1027', 'direction': 'Scratch',
         'seq': 1, 'is_substitute': False, 'is_extra': False},
    ]}
    with patch('apps.notifications.digests._collect_day', return_value=day_map):
        assert digests.send_morning_digest(day=TODAY) == 0
    assert NotificationMessage.objects.count() == 0


@pytest.mark.django_db
def test_disabled_holds_back_what_is_already_queued(linked):
    """Щёлкнув тумблер, человек ждёт тишины немедленно — а не «кроме того,
    что успело накопиться в очереди»."""
    NotificationMessage.objects.create(
        kind=KIND_MORNING_DIGEST, channel='dm', chat_id=555,
        text='утро', dedup_key='pending-1', status=STATUS_QUEUED,
        recipient_teacher=linked,
    )
    _disable()
    with patch('apps.notifications.dispatcher.telegram.send_message',
               return_value=SendResult(ok=True)) as send:
        assert dispatcher.dispatch() == 0
    send.assert_not_called()

    row = NotificationMessage.objects.get(dedup_key='pending-1')
    assert row.status == STATUS_QUEUED, 'сообщение не потеряно, просто ждёт'


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_re_enabling_restores_delivery(linked):
    _disable()
    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=linked.id, text='пропущено',
        dedup_prefix='makeup_assigned:81',
        source_kind='absence_resolution', source_id=81, also_to_group_chat=True)

    row = NotificationSettings.load()
    row.is_enabled = True
    row.save(update_fields=['is_enabled'])

    services.notify_teacher(
        kind=KIND_MAKEUP_ASSIGNED, teacher_id=linked.id, text='после включения',
        dedup_prefix='makeup_assigned:82',
        source_kind='absence_resolution', source_id=82, also_to_group_chat=True)

    texts = list(NotificationMessage.objects.values_list('text', flat=True))
    assert any('после включения' in t for t in texts)
    assert not any('пропущено' in t for t in texts), \
        'события за время паузы не должны всплыть задним числом'
