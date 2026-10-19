"""
Email-напоминания преподавателю: «Расписание на сегодня» и «Урок не записан».

Данные — реальный план занятий (фикстура sched_setup из apps.scheduling): группа A
преподавателя A, урок №1 — пн 2026-06-01 10:00, длительность 60 минут, у
преподавателя A есть учётка '__sched_a__@t.local'; у преподавателя B учётки нет.
"""
from __future__ import annotations

import datetime

import pytest
from django.test import override_settings

from apps.core.utils.dates import MSK
from apps.notifications import reminders
from apps.notifications.constants import (
    CHANNEL_EMAIL, KIND_LESSON_UNRECORDED, KIND_MORNING_DIGEST, STATUS_FAILED,
    UNRECORDED_MAX_REPEATS,
)
from apps.notifications.models import NotificationMessage, NotificationSettings
from apps.scheduling import repository as scheduling_repo
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import DONE
from apps.scheduling.tests.conftest import sched_setup  # noqa: F401 — переиспользуем фикстуру
from apps.teachers.models import Teacher

DAY = datetime.date(2026, 6, 1)
ACCOUNT_EMAIL = '__sched_a__@t.local'


def at(day: datetime.date, hh: int, mm: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hh, mm), tzinfo=MSK)


def _disable():
    row = NotificationSettings.load()
    row.is_enabled = False
    row.save(update_fields=['is_enabled'])


@pytest.fixture
def planned(sched_setup):  # noqa: F811
    scheduling_repo.generate_for_group(sched_setup['group_a'])
    sched_setup['lesson1'] = PlannedLesson.objects.get(group_id=sched_setup['group_a'], seq=1)
    yield sched_setup
    # sched_setup чистит преподавателей сырым DELETE — строки очереди со ссылкой
    # на них убираем раньше, иначе отложенная проверка FK валит teardown.
    NotificationMessage.objects.all().delete()


def _emails(kind: str) -> list[NotificationMessage]:
    return list(NotificationMessage.objects.filter(kind=kind, channel=CHANNEL_EMAIL)
                .order_by('id'))


# --- Расписание на сегодня -------------------------------------------------------

@pytest.mark.django_db
@override_settings(PUBLIC_URL='https://journal.example.test/')
def test_schedule_letter_goes_to_account_email_with_lessons(planned):
    # Email карточки отличается от учётки — канон учётка.
    Teacher.objects.filter(id=planned['teacher_a']).update(email='card@example.test')

    assert reminders.send_teacher_schedule(DAY) == 1

    [row] = _emails(KIND_MORNING_DIGEST)
    assert row.recipient_email == ACCOUNT_EMAIL
    assert row.recipient_teacher_id == planned['teacher_a']
    assert row.chat_id is None
    assert '01.06' in row.subject
    assert '10:00 — __sched_group_A__ (__sched_dir__) — урок №1' in row.text
    assert 'https://journal.example.test/teacher/calendar' in row.text
    assert 'Eduson Kids' in row.text
    # Имён учеников в письме нет — только группа и номер урока.
    assert '__sched_student__' not in row.text


@pytest.mark.django_db
def test_schedule_skips_days_without_lessons_and_is_idempotent(planned):
    assert reminders.send_teacher_schedule(DAY + datetime.timedelta(days=1)) == 0
    assert reminders.send_teacher_schedule(DAY) == 1
    assert reminders.send_teacher_schedule(DAY) == 0
    assert len(_emails(KIND_MORNING_DIGEST)) == 1


@pytest.mark.django_db
def test_schedule_uses_card_email_when_teacher_has_no_account(planned):
    """Преподаватель B — без учётки; его урок назначаем ему заменой."""
    PlannedLesson.objects.filter(pk=planned['lesson1'].pk).update(
        substitute_teacher_id=planned['teacher_b'])
    assert reminders.send_teacher_schedule(DAY) == 0  # у B нет адреса вовсе

    Teacher.objects.filter(id=planned['teacher_b']).update(email=' b-card@example.test ')
    assert reminders.send_teacher_schedule(DAY) == 1
    [row] = _emails(KIND_MORNING_DIGEST)
    assert row.recipient_email == 'b-card@example.test'
    assert '(замена)' in row.text


@pytest.mark.django_db
def test_schedule_obeys_global_switch(planned):
    _disable()
    assert reminders.send_teacher_schedule(DAY) == 0
    assert NotificationMessage.objects.count() == 0


@pytest.mark.django_db
def test_schedule_build_failure_costs_one_letter(planned, monkeypatch):
    def boom(**kwargs):
        raise ValueError('кривая строка')
    monkeypatch.setattr('apps.notifications.email_messages.teacher_schedule', boom)

    assert reminders.send_teacher_schedule(DAY) == 0
    row = NotificationMessage.objects.get()
    assert row.status == STATUS_FAILED
    assert row.dedup_key.endswith(':error')
    assert 'кривая строка' in row.last_error


# --- Урок не записан -------------------------------------------------------------

@pytest.mark.django_db
def test_unrecorded_first_letter_after_lesson_end(planned):
    assert reminders.send_unrecorded_reminders(at(DAY, 10, 30)) == 0  # урок ещё идёт
    assert reminders.send_unrecorded_reminders(at(DAY, 11, 15)) == 1
    assert reminders.send_unrecorded_reminders(at(DAY, 12, 0)) == 0   # то же окно

    [row] = _emails(KIND_LESSON_UNRECORDED)
    assert row.recipient_email == ACCOUNT_EMAIL
    assert row.dedup_key == f'email:unrecorded:{planned["lesson1"].pk}:0'
    assert (row.source_kind, row.source_id) == ('planned_lesson', planned['lesson1'].pk)
    assert row.subject.startswith('Урок не записан')
    assert 'урок №1' in row.text
    assert '__sched_student__' not in row.text


@pytest.mark.django_db
def test_unrecorded_window_closes_after_three_hours(planned):
    assert reminders.send_unrecorded_reminders(at(DAY, 14, 0)) == 0  # конец 11:00 + 3 ч


@pytest.mark.django_db
def test_unrecorded_repeats_daily_at_most_n_times(planned):
    pk = planned['lesson1'].pk
    for k in range(UNRECORDED_MAX_REPEATS + 2):
        reminders.send_unrecorded_reminders(at(DAY + datetime.timedelta(days=k), 11, 30))

    keys = [r.dedup_key for r in _emails(KIND_LESSON_UNRECORDED)]
    assert keys == [f'email:unrecorded:{pk}:{k}' for k in range(UNRECORDED_MAX_REPEATS + 1)]
    repeat = _emails(KIND_LESSON_UNRECORDED)[1]
    assert repeat.subject.startswith('Напоминание: урок не записан')


@pytest.mark.django_db
def test_recorded_lesson_gets_no_letter(planned):
    PlannedLesson.objects.filter(pk=planned['lesson1'].pk).update(status=DONE)
    assert reminders.send_unrecorded_reminders(at(DAY, 11, 30)) == 0


@pytest.mark.django_db
def test_repeat_stops_once_lesson_is_recorded(planned):
    reminders.send_unrecorded_reminders(at(DAY, 11, 30))
    PlannedLesson.objects.filter(pk=planned['lesson1'].pk).update(status=DONE)
    reminders.send_unrecorded_reminders(at(DAY + datetime.timedelta(days=1), 11, 30))
    assert len(_emails(KIND_LESSON_UNRECORDED)) == 1


@pytest.mark.django_db
def test_unrecorded_goes_to_substitute(planned):
    PlannedLesson.objects.filter(pk=planned['lesson1'].pk).update(
        substitute_teacher_id=planned['teacher_b'])
    Teacher.objects.filter(id=planned['teacher_b']).update(email='b-card@example.test')
    reminders.send_unrecorded_reminders(at(DAY, 11, 30))
    [row] = _emails(KIND_LESSON_UNRECORDED)
    assert row.recipient_email == 'b-card@example.test'
    assert row.recipient_teacher_id == planned['teacher_b']


@pytest.mark.django_db
def test_unrecorded_obeys_global_switch(planned):
    _disable()
    assert reminders.send_unrecorded_reminders(at(DAY, 11, 30)) == 0


@pytest.mark.parametrize('minutes, expected', [
    (-1, None), (0, 0), (179, 0), (180, None),
    (24 * 60, 1), (24 * 60 + 179, 1), (24 * 60 + 180, None),
    (3 * 24 * 60 + 10, 3), (4 * 24 * 60 + 10, None),
])
def test_unrecorded_attempt_windows(minutes, expected):
    end = at(DAY, 11, 0)
    now = end + datetime.timedelta(minutes=minutes)
    assert reminders.unrecorded_attempt(end=end, now=now) == expected
