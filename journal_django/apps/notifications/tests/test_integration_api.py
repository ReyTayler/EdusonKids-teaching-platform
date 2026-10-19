"""Служебные эндпоинты бота: доступ только по общему секрету."""
from __future__ import annotations

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.notifications.models import TelegramRecipient, TelegramUser
from apps.teachers.models import Teacher

IDENTIFY = '/api/integrations/telegram/identify'
MY = '/api/integrations/telegram/my'


@pytest.mark.django_db
@override_settings(BOT_SERVICE_TOKEN='s3cret')
def test_identify_requires_token():
    response = APIClient().post(IDENTIFY, {'telegram_id': 1, 'full_name': 'Иван'},
                                format='json')
    assert response.status_code in (401, 403)


@pytest.mark.django_db
@override_settings(BOT_SERVICE_TOKEN='s3cret')
def test_identify_rejects_wrong_token():
    client = APIClient()
    response = client.post(IDENTIFY, {'telegram_id': 1, 'full_name': 'Иван'},
                           format='json', HTTP_X_BOT_TOKEN='wrong')
    assert response.status_code in (401, 403)


@pytest.mark.django_db
@override_settings(BOT_SERVICE_TOKEN='s3cret')
def test_identify_upserts_telegram_user():
    client = APIClient()
    payload = {'telegram_id': 777, 'username': 'anna', 'full_name': 'Анна Петрова'}
    first = client.post(IDENTIFY, payload, format='json', HTTP_X_BOT_TOKEN='s3cret')
    assert first.status_code == 204

    payload['username'] = 'anna_new'
    second = client.post(IDENTIFY, payload, format='json', HTTP_X_BOT_TOKEN='s3cret')
    assert second.status_code == 204

    assert TelegramUser.objects.filter(chat_id=777).count() == 1
    assert TelegramUser.objects.get(chat_id=777).username == 'anna_new'


@pytest.mark.django_db
@override_settings(BOT_SERVICE_TOKEN='s3cret')
def test_my_returns_404_for_unlinked_account():
    TelegramUser.objects.create(chat_id=888, username='bob', full_name='Боб')
    response = APIClient().get(f'{MY}?telegram_id=888', HTTP_X_BOT_TOKEN='s3cret')
    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(BOT_SERVICE_TOKEN='s3cret')
def test_my_returns_events_for_linked_teacher():
    teacher = Teacher.objects.create(name='Анна', created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=999, username='anna', full_name='Анна')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)

    response = APIClient().get(f'{MY}?telegram_id=999', HTTP_X_BOT_TOKEN='s3cret')
    assert response.status_code == 200
    assert 'events' in response.data
    assert isinstance(response.data['events'], list)


@pytest.mark.django_db
@override_settings(BOT_SERVICE_TOKEN='')
def test_empty_configured_token_denies_everyone():
    """Пустой секрет в настройках не должен означать «пускать всех»."""
    response = APIClient().post(IDENTIFY, {'telegram_id': 1, 'full_name': 'И'},
                                format='json', HTTP_X_BOT_TOKEN='')
    assert response.status_code in (401, 403)
