"""Вечерний дайджест: только личка, обязательная приписка, идемпотентность."""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.notifications import digests
from apps.notifications.constants import CHANNEL_DM, KIND_FILL_DIGEST
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.teachers.models import Teacher

TODAY = datetime.date(2026, 8, 3)


@pytest.fixture
def linked(db):
    teacher = Teacher.objects.create(name='Анна Петрова', created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=555, username='anna', full_name='Анна')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
    return teacher


def _unfilled(teacher_id: int) -> list[dict]:
    return [{
        'kind': 'planned', 'id': 1, 'group_id': 7, 'group_name': 'ПИ1054',
        'teacher_id': teacher_id, 'teacher_name': 'Анна Петрова',
        'direction_name': 'Python', 'direction_color': '#000',
        'lesson_number': 5.5, 'seq': 11, 'date': '2026-08-02', 'time': '16:00',
    }]


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_fill_digest_is_dm_only_and_has_footer(linked):
    with patch('apps.notifications.digests.fill_service.unfilled_lessons',
               return_value=_unfilled(linked.id)):
        digests.send_fill_digest(day=TODAY)

    rows = list(NotificationMessage.objects.all())
    assert len(rows) == 1
    assert rows[0].channel == CHANNEL_DM
    assert rows[0].kind == KIND_FILL_DIGEST
    assert 'Если уроков не было, сообщите менеджеру или администратору.' in rows[0].text


@pytest.mark.django_db
def test_fill_digest_shows_ordinal_lesson_number(linked):
    """Номер урока — порядковый seq (11), а не половинный вес lesson_number (5.5).

    Внутренний вес на 45-минутных курсах равен seq × 0.5 и служит расчёту денег;
    преподавателю показывается нормальный счёт.
    """
    with patch('apps.notifications.digests.fill_service.unfilled_lessons',
               return_value=_unfilled(linked.id)):
        digests.send_fill_digest(day=TODAY)
    text = NotificationMessage.objects.get().text
    assert 'урок №11' in text
    assert 'урок №5' not in text


@pytest.mark.django_db
def test_fill_digest_is_idempotent_within_a_day(linked):
    with patch('apps.notifications.digests.fill_service.unfilled_lessons',
               return_value=_unfilled(linked.id)):
        digests.send_fill_digest(day=TODAY)
        digests.send_fill_digest(day=TODAY)
    assert NotificationMessage.objects.filter(kind=KIND_FILL_DIGEST).count() == 1


@pytest.mark.django_db
def test_fill_digest_repeats_next_day(linked):
    with patch('apps.notifications.digests.fill_service.unfilled_lessons',
               return_value=_unfilled(linked.id)):
        digests.send_fill_digest(day=TODAY)
        digests.send_fill_digest(day=TODAY + datetime.timedelta(days=1))
    assert NotificationMessage.objects.filter(kind=KIND_FILL_DIGEST).count() == 2


@pytest.mark.django_db
def test_fill_digest_survives_extra_lesson_without_direction(linked):
    """Незаполненный доп.урок не должен гасить рассылку.

    fill_service отдаёт у доп.уроков direction_name=None (у отработки нет
    направления — так же рисует и вкладка «Заполнить»). Дайджест собирает
    payload'ы всей школы одним списком, поэтому исключение на одной строке
    оставляло без письма ВСЕХ преподавателей (прод, 06.08.2026).
    """
    rows = _unfilled(linked.id) + [{
        'kind': 'extra', 'id': 104, 'group_id': 7, 'group_name': 'БГ16',
        'teacher_id': linked.id, 'teacher_name': 'Анна Петрова',
        'direction_name': None, 'direction_color': None,
        'lesson_number': None, 'seq': None, 'date': '2026-08-06', 'time': '14:00',
    }]
    with patch('apps.notifications.digests.fill_service.unfilled_lessons',
               return_value=rows):
        digests.send_fill_digest(day=TODAY)

    text = NotificationMessage.objects.get(kind=KIND_FILL_DIGEST).text
    assert 'БГ16 — доп.урок' in text


@pytest.mark.django_db
def test_unlinked_teacher_is_skipped(db):
    teacher = Teacher.objects.create(name='Без телеги', created_at='2026-08-01T00:00:00Z')
    with patch('apps.notifications.digests.fill_service.unfilled_lessons',
               return_value=_unfilled(teacher.id)):
        digests.send_fill_digest(day=TODAY)
    assert NotificationMessage.objects.count() == 0
