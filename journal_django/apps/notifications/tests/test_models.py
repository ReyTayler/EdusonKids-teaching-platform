"""Тесты моделей notifications: формы данных и защита от дублей."""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.notifications.constants import CHANNEL_DM, KIND_MORNING_DIGEST, STATUS_QUEUED
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.teachers.models import Teacher


@pytest.mark.django_db
def test_telegram_user_chat_id_unique():
    TelegramUser.objects.create(chat_id=111, username='ivanov', full_name='Иван')
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TelegramUser.objects.create(chat_id=111, username='other', full_name='Другой')


@pytest.mark.django_db
def test_recipient_is_one_per_teacher():
    teacher = Teacher.objects.create(name='Анна Петрова', created_at='2026-08-01T00:00:00Z')
    tg1 = TelegramUser.objects.create(chat_id=201, username='anna', full_name='Анна')
    tg2 = TelegramUser.objects.create(chat_id=202, username='anna2', full_name='Анна 2')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg1)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg2)


@pytest.mark.django_db
def test_dedup_key_is_unique():
    NotificationMessage.objects.create(
        kind=KIND_MORNING_DIGEST, channel=CHANNEL_DM, chat_id=111,
        text='первое', dedup_key='morning:1:2026-08-03', status=STATUS_QUEUED,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            NotificationMessage.objects.create(
                kind=KIND_MORNING_DIGEST, channel=CHANNEL_DM, chat_id=111,
                text='второе', dedup_key='morning:1:2026-08-03', status=STATUS_QUEUED,
            )


@pytest.mark.django_db
def test_one_telegram_account_cannot_serve_two_teachers():
    """Две привязки на один аккаунт — всегда ошибка ввода: человек начал бы
    получать чужие уведомления. Запрет на уровне БД, а не только пометкой в UI."""
    first = Teacher.objects.create(name='Первый', created_at='2026-08-01T00:00:00Z')
    second = Teacher.objects.create(name='Второй', created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=301, username='shared', full_name='Общий')
    TelegramRecipient.objects.create(teacher=first, telegram_user=tg)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TelegramRecipient.objects.create(teacher=second, telegram_user=tg)
