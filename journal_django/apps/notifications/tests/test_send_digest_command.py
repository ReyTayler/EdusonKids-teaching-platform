"""Команда ручной проверки дайджестов: предпросмотр, адресность, повтор."""
from __future__ import annotations

import datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.notifications.constants import KIND_MORNING_DIGEST
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.teachers.models import Teacher

TODAY = datetime.date(2026, 8, 3)


@pytest.fixture
def two_linked(db):
    """Два привязанных преподавателя — чтобы проверить адресность."""
    out = []
    for name, chat_id, nick in (('Первый', 901, 'first'), ('Второй', 902, 'second')):
        teacher = Teacher.objects.create(name=name, created_at='2026-08-01T00:00:00Z')
        tg = TelegramUser.objects.create(chat_id=chat_id, username=nick, full_name=name)
        TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
        out.append(teacher)
    return out


def _day_map(teachers):
    return {
        t.id: [{'time': '12:00', 'group': 'СИ1027', 'direction': 'Scratch',
                'seq': 1, 'is_substitute': False, 'is_extra': False}]
        for t in teachers
    }


@pytest.mark.django_db
def test_dry_run_shows_text_and_sends_nothing(two_linked):
    out = StringIO()
    with patch('apps.notifications.digests._collect_day',
               return_value=_day_map(two_linked)):
        call_command('send_digest', 'morning', '--date', TODAY.isoformat(),
                     '--dry-run', stdout=out)

    assert 'Доброе утро' in out.getvalue()
    assert 'dry-run' in out.getvalue()
    assert NotificationMessage.objects.count() == 0


@pytest.mark.django_db
def test_teacher_id_limits_delivery_to_one_person(two_linked):
    """Главный сценарий: проверить на себе, не потревожив остальных."""
    first, second = two_linked
    with patch('apps.notifications.digests._collect_day',
               return_value=_day_map(two_linked)):
        call_command('send_digest', 'morning', '--date', TODAY.isoformat(),
                     '--teacher-id', str(first.id), stdout=StringIO())

    rows = list(NotificationMessage.objects.filter(kind=KIND_MORNING_DIGEST))
    assert len(rows) == 1
    assert rows[0].recipient_teacher_id == first.id
    assert rows[0].chat_id == 901


@pytest.mark.django_db
def test_second_run_without_force_sends_nothing(two_linked):
    first, _second = two_linked
    with patch('apps.notifications.digests._collect_day',
               return_value=_day_map(two_linked)):
        for _ in range(2):
            call_command('send_digest', 'morning', '--date', TODAY.isoformat(),
                         '--teacher-id', str(first.id), stdout=StringIO())

    assert NotificationMessage.objects.filter(kind=KIND_MORNING_DIGEST).count() == 1


@pytest.mark.django_db
def test_force_allows_repeat_within_the_same_day(two_linked):
    """Чтобы можно было слать себе несколько раз, подбирая формулировки."""
    first, _second = two_linked
    with patch('apps.notifications.digests._collect_day',
               return_value=_day_map(two_linked)):
        call_command('send_digest', 'morning', '--date', TODAY.isoformat(),
                     '--teacher-id', str(first.id), stdout=StringIO())
        call_command('send_digest', 'morning', '--date', TODAY.isoformat(),
                     '--teacher-id', str(first.id), '--force', stdout=StringIO())

    assert NotificationMessage.objects.filter(kind=KIND_MORNING_DIGEST).count() == 2


@pytest.mark.django_db
def test_unknown_teacher_is_a_clear_error(two_linked):
    with pytest.raises(CommandError, match='не найден'):
        call_command('send_digest', 'morning', '--teacher-id', '999999',
                     stdout=StringIO())


@pytest.mark.django_db
def test_bad_date_is_a_clear_error(two_linked):
    with pytest.raises(CommandError, match='ГГГГ-ММ-ДД'):
        call_command('send_digest', 'morning', '--date', '03.08.2026',
                     stdout=StringIO())


@pytest.mark.django_db
def test_empty_result_explains_why(two_linked):
    """Пустой результат — норма, но человек должен понять причину."""
    out = StringIO()
    with patch('apps.notifications.digests._collect_day', return_value={}):
        call_command('send_digest', 'morning', '--date', TODAY.isoformat(),
                     stdout=out)
    assert 'нет занятий' in out.getvalue()
    assert NotificationMessage.objects.count() == 0
