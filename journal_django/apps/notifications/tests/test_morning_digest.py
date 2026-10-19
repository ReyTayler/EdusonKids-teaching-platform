"""Утренний дайджест: состав, адресаты, отсутствие пустых сообщений."""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.notifications import digests
from apps.notifications.constants import CHANNEL_DM, CHANNEL_GROUP, KIND_MORNING_DIGEST
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.scheduling import repository as scheduling_repo
from apps.scheduling.tests.conftest import sched_setup  # noqa: F401 — переиспользуем фикстуру
from apps.teachers.models import Teacher

TODAY = datetime.date(2026, 8, 3)


@pytest.fixture
def linked(db):
    teacher = Teacher.objects.create(name='Анна Петрова', created_at='2026-08-01T00:00:00Z')
    tg = TelegramUser.objects.create(chat_id=555, username='anna', full_name='Анна')
    TelegramRecipient.objects.create(teacher=teacher, telegram_user=tg)
    return teacher


@pytest.mark.django_db
def test_teacher_without_lessons_gets_no_message(linked):
    """Будить человека в 8 утра ради «сегодня уроков нет» смысла нет."""
    with patch('apps.notifications.digests._collect_day', return_value={}):
        digests.send_morning_digest(day=TODAY)
    assert NotificationMessage.objects.count() == 0


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=-100500)
def test_morning_digest_goes_only_to_dm(linked):
    """Персональный список в общий чат не дублируется: 100 сообщений каждое утро
    сделали бы чат нечитаемым (спека, п. 2.5)."""
    day_map = {linked.id: [
        {'time': '12:00', 'group': 'СИ1027', 'direction': 'Scratch',
         'seq': 1, 'is_substitute': False, 'is_extra': False},
    ]}
    with patch('apps.notifications.digests._collect_day', return_value=day_map):
        digests.send_morning_digest(day=TODAY)

    rows = list(NotificationMessage.objects.all())
    assert len(rows) == 1
    assert rows[0].channel == CHANNEL_DM
    assert rows[0].kind == KIND_MORNING_DIGEST
    assert not NotificationMessage.objects.filter(channel=CHANNEL_GROUP).exists()


@pytest.mark.django_db
def test_morning_digest_is_idempotent_within_a_day(linked):
    day_map = {linked.id: [
        {'time': '12:00', 'group': 'СИ1027', 'direction': 'Scratch',
         'seq': 1, 'is_substitute': False, 'is_extra': False},
    ]}
    with patch('apps.notifications.digests._collect_day', return_value=day_map):
        digests.send_morning_digest(day=TODAY)
        digests.send_morning_digest(day=TODAY)
    assert NotificationMessage.objects.filter(kind=KIND_MORNING_DIGEST).count() == 1


@pytest.mark.django_db
def test_unlinked_teacher_is_skipped(db):
    teacher = Teacher.objects.create(name='Без телеги', created_at='2026-08-01T00:00:00Z')
    day_map = {teacher.id: [
        {'time': '12:00', 'group': 'СИ1027', 'direction': 'Scratch',
         'seq': 1, 'is_substitute': False, 'is_extra': False},
    ]}
    with patch('apps.notifications.digests._collect_day', return_value=day_map):
        digests.send_morning_digest(day=TODAY)
    assert NotificationMessage.objects.count() == 0


# --- Интеграционный тест сборки: реальные плановые занятия, не мок --------


@pytest.mark.django_db
def test_collect_day_groups_by_effective_teacher_and_marks_substitute(sched_setup):  # noqa: F811
    """
    Проверка на реальных данных (фикстура sched_setup из apps.scheduling): группа A
    стартует в понедельник 2026-06-01 10:00 у преподавателя A. Занятие должно
    попасть в список A на эту дату. После назначения преподавателя B заменой на
    это же занятие — оно обязано уехать в список B с пометкой is_substitute=True
    и пропасть из списка A (спека, п. «замена» — иначе человек придёт вести
    чужую группу как свою).
    """
    from apps.scheduling.models import PlannedLesson

    s = sched_setup
    scheduling_repo.generate_for_group(s['group_a'])
    day = datetime.date(2026, 6, 1)

    day_map = digests._collect_day(day)
    assert s['teacher_a'] in day_map
    items = day_map[s['teacher_a']]
    assert len(items) == 1
    assert items[0]['group'] == s['group_a_name']
    assert items[0]['seq'] == 1
    assert items[0]['is_substitute'] is False

    # Назначаем преподавателя B заменой на это занятие.
    PlannedLesson.objects.filter(group_id=s['group_a'], seq=1).update(
        substitute_teacher_id=s['teacher_b'])

    day_map2 = digests._collect_day(day)
    assert s['teacher_a'] not in day_map2
    assert s['teacher_b'] in day_map2
    b_items = day_map2[s['teacher_b']]
    assert len(b_items) == 1
    assert b_items[0]['is_substitute'] is True
