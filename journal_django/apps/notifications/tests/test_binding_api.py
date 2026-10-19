"""Привязка Telegram: менеджер видит, меняет только админ."""
from __future__ import annotations

import pytest

from apps.notifications.models import TelegramRecipient, TelegramUser
from apps.teachers.models import Teacher


@pytest.fixture
def teacher(db):
    return Teacher.objects.create(name='Анна Петрова', created_at='2026-08-01T00:00:00Z')


@pytest.fixture
def tg_user(db):
    return TelegramUser.objects.create(chat_id=777, username='anna', full_name='Анна')


@pytest.mark.django_db
def test_manager_cannot_list_known_telegram_accounts(manager_client, tg_user):
    """
    Список аккаунтов бота закрыт от менеджера.

    Он существует только чтобы выбрать аккаунт при привязке, а привязывать
    менеджер не может (`test_manager_cannot_bind` ниже). Пока список был
    открыт, менеджер, открывая любую карточку преподавателя, выкачивал имена,
    ники и chat_id всех, кто писал боту, и то, к кому они привязаны —
    выпадашки в интерфейсе не видел, но данные получал.
    """
    assert manager_client.get('/api/admin/telegram-users').status_code == 403


@pytest.mark.django_db
def test_admin_can_list_known_telegram_accounts(admin_client, tg_user):
    response = admin_client.get('/api/admin/telegram-users')
    assert response.status_code == 200
    assert any(row['chat_id'] == 777 for row in response.data['rows'])


@pytest.mark.django_db
def test_manager_cannot_bind(manager_client, teacher, tg_user):
    response = manager_client.post(
        f'/api/admin/teachers/{teacher.id}/telegram',
        {'chat_id': 777}, format='json')
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_binds_and_unbinds(admin_client, teacher, tg_user):
    bound = admin_client.post(
        f'/api/admin/teachers/{teacher.id}/telegram', {'chat_id': 777}, format='json')
    assert bound.status_code in (200, 201)
    assert TelegramRecipient.objects.filter(teacher=teacher, is_active=True).exists()

    unbound = admin_client.delete(f'/api/admin/teachers/{teacher.id}/telegram')
    assert unbound.status_code in (200, 204)
    assert not TelegramRecipient.objects.filter(teacher=teacher).exists()


@pytest.mark.django_db
def test_rebinding_reactivates_after_block(admin_client, teacher, tg_user):
    TelegramRecipient.objects.create(
        teacher=teacher, telegram_user=tg_user, is_active=False,
        blocked_reason='bot was blocked by the user')

    response = admin_client.post(
        f'/api/admin/teachers/{teacher.id}/telegram', {'chat_id': 777}, format='json')
    assert response.status_code in (200, 201)
    recipient = TelegramRecipient.objects.get(teacher=teacher)
    assert recipient.is_active is True
    assert recipient.blocked_reason is None


@pytest.mark.django_db
def test_anonymous_is_denied(anon_client, teacher):
    assert anon_client.get('/api/admin/telegram-users').status_code in (401, 403)
