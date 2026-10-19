"""
Сбой сборки письма одному преподавателю не гасит рассылку остальным.

Дайджест собирает сообщения всей школы одним проходом, поэтому до появления
этих гарантий исключение на одной строке оставляло без письма ВСЕХ (прод,
06.08.2026: одна незаполненная отработка без направления). Теперь пострадавший
получает строку «Не доставлено» в журнале, а остальные — свои письма.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from apps.notifications import digests, messages
from apps.notifications.constants import (
    CHANNEL_DM, KIND_FILL_DIGEST, KIND_MORNING_DIGEST, STATUS_FAILED, STATUS_QUEUED,
)
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.teachers.models import Teacher

TODAY = datetime.date(2026, 8, 6)


def _link(name: str, chat_id: int) -> Teacher:
    teacher = Teacher.objects.create(name=name, created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=chat_id, username=None, full_name=name)
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
    return teacher


@pytest.fixture
def two_teachers(db) -> tuple[Teacher, Teacher]:
    """Первый — со сломанным письмом, второй — с обычным."""
    return _link('Примерова Анна', 733), _link('Образцова Мария', 814)


def _unfilled_row(teacher: Teacher, group: str) -> dict:
    return {
        'kind': 'planned', 'id': 1, 'group_id': 7, 'group_name': group,
        'teacher_id': teacher.id, 'teacher_name': teacher.name,
        'direction_name': 'Python', 'direction_color': '#000',
        'lesson_number': 5.0, 'seq': 10, 'date': '2026-08-05', 'time': '16:00',
    }


class _BreakFor:
    """Ломает сборку текста ровно для одной группы, остальным даёт собраться."""

    def __init__(self, broken_group: str, real):
        self.broken_group = broken_group
        self.real = real

    def __call__(self, *, items, **kwargs):
        if any(i['group'] == self.broken_group for i in items):
            raise AttributeError("'NoneType' object has no attribute 'replace'")
        return self.real(items=items, **kwargs)


@pytest.fixture
def broken_fill(two_teachers):
    """Рассылка, где письмо первому преподавателю не собирается."""
    broken, healthy = two_teachers
    rows = [_unfilled_row(broken, 'ВДГ18'), _unfilled_row(healthy, 'ПИ309')]
    breaker = _BreakFor('ВДГ18', messages.fill_digest)
    return broken, healthy, rows, breaker


@pytest.mark.django_db
def test_broken_letter_does_not_hold_back_the_others(broken_fill):
    broken, healthy, rows, breaker = broken_fill

    with patch('apps.notifications.digests.fill_service.unfilled_lessons', return_value=rows), \
         patch('apps.notifications.digests.messages.fill_digest', side_effect=breaker):
        digests.send_fill_digest(day=TODAY)

    sent = NotificationMessage.objects.get(status=STATUS_QUEUED)
    assert sent.recipient_teacher_id == healthy.id
    assert 'ПИ309' in sent.text


@pytest.mark.django_db
def test_failure_is_visible_in_the_journal_with_its_reason(broken_fill):
    """Иначе провал ничем себя не проявляет: сообщений просто нет."""
    broken, _healthy, rows, breaker = broken_fill

    with patch('apps.notifications.digests.fill_service.unfilled_lessons', return_value=rows), \
         patch('apps.notifications.digests.messages.fill_digest', side_effect=breaker):
        digests.send_fill_digest(day=TODAY)

    row = NotificationMessage.objects.get(status=STATUS_FAILED)
    assert row.recipient_teacher_id == broken.id
    assert row.kind == KIND_FILL_DIGEST
    assert row.channel == CHANNEL_DM
    assert row.chat_id == 733
    assert 'AttributeError' in row.last_error
    # Текст письма собрать не удалось — в журнале лежит объяснение, а не пустота.
    assert row.text


@pytest.mark.django_db
def test_failure_is_recorded_once_per_day(broken_fill):
    """Повторный прогон (ретрай задачи, ручной запуск) не плодит строк об одном сбое."""
    _broken, _healthy, rows, breaker = broken_fill

    with patch('apps.notifications.digests.fill_service.unfilled_lessons', return_value=rows), \
         patch('apps.notifications.digests.messages.fill_digest', side_effect=breaker):
        digests.send_fill_digest(day=TODAY)
        digests.send_fill_digest(day=TODAY)

    assert NotificationMessage.objects.filter(status=STATUS_FAILED).count() == 1


@pytest.mark.django_db
def test_letter_still_goes_out_the_same_day_once_the_bug_is_fixed(broken_fill):
    """Ключ у записи о сбое свой, поэтому починка в тот же день не упирается
    в идемпотентность: преподаватель получает нормальное письмо с опозданием."""
    broken, _healthy, rows, breaker = broken_fill

    with patch('apps.notifications.digests.fill_service.unfilled_lessons', return_value=rows), \
         patch('apps.notifications.digests.messages.fill_digest', side_effect=breaker):
        digests.send_fill_digest(day=TODAY)

    with patch('apps.notifications.digests.fill_service.unfilled_lessons', return_value=rows):
        queued = digests.send_fill_digest(day=TODAY)

    assert queued == 1
    assert NotificationMessage.objects.filter(
        status=STATUS_QUEUED, recipient_teacher=broken).exists()


@pytest.mark.django_db
def test_failed_row_is_never_sent(broken_fill):
    """Строка о сбое — запись в журнале, а не сообщение: диспетчер её не берёт."""
    _broken, _healthy, rows, breaker = broken_fill

    with patch('apps.notifications.digests.fill_service.unfilled_lessons', return_value=rows), \
         patch('apps.notifications.digests.messages.fill_digest', side_effect=breaker):
        digests.send_fill_digest(day=TODAY)

    from apps.notifications import dispatcher

    with patch('apps.notifications.telegram.send_message') as send:
        send.return_value = type('R', (), {'ok': True, 'error': None, 'blocked': False,
                                           'retry_after': None})()
        dispatcher.dispatch()

    assert send.call_count == 1  # ушло только письмо здорового преподавателя
    failed = NotificationMessage.objects.get(status=STATUS_FAILED)
    assert failed.sent_at is None
    assert failed.attempts == 0


@pytest.mark.django_db
def test_morning_digest_guards_each_teacher_too(two_teachers):
    """Та же защита в утренней рассылке: дыра там ровно та же."""
    broken, healthy = two_teachers
    day_map = {
        broken.id: [{'time': '12:00', 'group': 'ВДГ18', 'direction': 'Веб-дизайн',
                     'seq': 1, 'is_substitute': False, 'is_extra': False}],
        healthy.id: [{'time': '14:00', 'group': 'ПИ309', 'direction': 'Python',
                      'seq': 2, 'is_substitute': False, 'is_extra': False}],
    }
    breaker = _BreakFor('ВДГ18', messages.morning_digest)

    with patch('apps.notifications.digests._collect_day', return_value=day_map), \
         patch('apps.notifications.digests.messages.morning_digest', side_effect=breaker):
        digests.send_morning_digest(day=TODAY)

    assert NotificationMessage.objects.get(status=STATUS_QUEUED).recipient_teacher_id == healthy.id
    failed = NotificationMessage.objects.get(status=STATUS_FAILED)
    assert failed.recipient_teacher_id == broken.id
    assert failed.kind == KIND_MORNING_DIGEST
