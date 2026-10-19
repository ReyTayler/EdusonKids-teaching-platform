"""
Email-напоминания семьям учеников: «урок завтра» (10:00) и «урок через час».

Урок №1 группы A — пн 2026-06-01 10:00 (фикстура sched_setup). Адреса —
выдуманные, домен example.test.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.core.utils.dates import MSK
from apps.notifications import reminders
from apps.notifications.constants import (
    CHANNEL_EMAIL, KIND_PARENT_DAY_BEFORE, KIND_PARENT_HOUR_BEFORE,
)
from apps.notifications.models import NotificationMessage, NotificationSettings
from apps.scheduling import repository as scheduling_repo
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import CANCELLED
from apps.scheduling.tests.conftest import sched_setup  # noqa: F401 — переиспользуем фикстуру
from apps.students.models import Student

DAY = datetime.date(2026, 6, 1)


def at(day: datetime.date, hh: int, mm: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hh, mm), tzinfo=MSK)


def _settings(*, enabled: bool = True, parents: bool = True) -> None:
    row = NotificationSettings.load()
    row.is_enabled = enabled
    row.parent_reminders_enabled = parents
    row.save(update_fields=['is_enabled', 'parent_reminders_enabled'])


@pytest.fixture
def family(sched_setup):  # noqa: F811
    """Ученик группы A с тремя адресами (один — дубль другого в другом регистре)."""
    scheduling_repo.generate_for_group(sched_setup['group_a'])
    Student.objects.filter(id=sched_setup['student_id']).update(
        full_name='Миша',
        email='kid@example.test',
        parent1_email='Parent@Example.test',
        parent2_email='parent@example.test',
    )
    sched_setup['lesson1'] = PlannedLesson.objects.get(group_id=sched_setup['group_a'], seq=1)
    yield sched_setup
    NotificationMessage.objects.all().delete()


def _rows(kind: str) -> list[NotificationMessage]:
    return list(NotificationMessage.objects.filter(kind=kind).order_by('recipient_email'))


# --- Выключатели -----------------------------------------------------------------

@pytest.mark.django_db
def test_parent_reminders_are_off_by_default(family):
    NotificationSettings.objects.all().delete()
    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 0
    assert reminders.send_parent_hour_before(at(DAY, 9)) == 0
    assert NotificationMessage.objects.count() == 0


@pytest.mark.django_db
def test_global_switch_silences_parents_too(family):
    _settings(enabled=False, parents=True)
    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 0
    assert reminders.send_parent_hour_before(at(DAY, 9)) == 0


# --- Урок завтра -----------------------------------------------------------------

@pytest.mark.django_db
def test_day_before_one_letter_per_address(family):
    _settings()
    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 2

    rows = _rows(KIND_PARENT_DAY_BEFORE)
    # Parent@Example.test и parent@example.test — один ящик, письмо одно
    # (уходит на тот адрес, что встретился первым — поле parent1).
    assert {r.recipient_email for r in rows} == {'Parent@Example.test', 'kid@example.test'}
    pk = family['lesson1'].pk
    assert {r.dedup_key for r in rows} == {
        f'email:{KIND_PARENT_DAY_BEFORE}:{pk}:parent@example.test',
        f'email:{KIND_PARENT_DAY_BEFORE}:{pk}:kid@example.test',
    }
    letter = rows[0]
    assert letter.channel == CHANNEL_EMAIL
    assert letter.recipient_teacher_id is None
    assert letter.subject == 'Напоминание: занятие завтра в 10:00'
    assert '01.06' in letter.text and '«__sched_dir__»' in letter.text
    assert 'Ученик: Миша.' in letter.text
    assert 'Eduson Kids' in letter.text
    # Ни имени преподавателя, ни названия группы — только то, что нужно семье.
    assert '__sched_A__' not in letter.text
    assert '__sched_group_A__' not in letter.text


@pytest.mark.django_db
def test_day_before_build_failure_costs_one_letter(family, monkeypatch):
    """Сбой сборки одного письма не обрывает рассылку остальным адресам."""
    _settings()
    real = reminders.email_messages.parent_day_before
    calls = {'n': 0}

    def flaky(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('broken template')
        return real(*args, **kwargs)

    monkeypatch.setattr(reminders.email_messages, 'parent_day_before', flaky)
    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 1
    assert len(_rows(KIND_PARENT_DAY_BEFORE)) == 1


@pytest.mark.django_db
def test_day_before_is_idempotent(family):
    _settings()
    reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10))
    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 0
    assert len(_rows(KIND_PARENT_DAY_BEFORE)) == 2


@pytest.mark.django_db
def test_day_before_skips_cancelled_lessons_and_students_without_email(family):
    _settings()
    PlannedLesson.objects.filter(pk=family['lesson1'].pk).update(status=CANCELLED)
    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 0

    PlannedLesson.objects.filter(pk=family['lesson1'].pk).update(status='pending')
    Student.objects.filter(id=family['student_id']).update(
        email='', parent1_email=None, parent2_email='   ')
    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 0


@pytest.mark.django_db
def test_siblings_with_shared_parent_get_one_letter_naming_both(family):
    _settings()
    Student.objects.filter(id=family['student_id']).update(email=None, parent2_email=None)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name, parent1_email, created_at) "
            "VALUES ('Маша', 'parent@example.test', NOW()) RETURNING id")
        sister = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s,%s,0,true)', [family['group_a'], sister])

    assert reminders.send_parent_day_before(at(DAY - datetime.timedelta(days=1), 10)) == 1
    [letter] = _rows(KIND_PARENT_DAY_BEFORE)
    assert 'Ученики: Миша, Маша.' in letter.text

    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [sister])
        cur.execute('DELETE FROM students WHERE id = %s', [sister])


# --- Урок через час --------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('hh, mm, expected', [
    (8, 50, 0),   # 70 минут до начала — рано
    (8, 55, 2),   # ровно 65 минут — граница включена
    (9, 0, 2),    # 60 минут
    (9, 5, 0),    # ровно 55 минут — граница исключена
])
def test_hour_before_window(family, hh, mm, expected):
    _settings()
    assert reminders.send_parent_hour_before(at(DAY, hh, mm)) == expected


@pytest.mark.django_db
def test_hour_before_ticks_every_five_minutes_without_duplicates(family):
    _settings()
    reminders.send_parent_hour_before(at(DAY, 8, 55))
    reminders.send_parent_hour_before(at(DAY, 9, 0))
    rows = _rows(KIND_PARENT_HOUR_BEFORE)
    assert len(rows) == 2
    assert rows[0].subject == 'Занятие через час — в 10:00'


@pytest.mark.django_db
def test_hour_before_window_crosses_midnight(family):
    """Урок в 00:00 следующего дня ловится тиком в 23:00 — окно на две даты."""
    _settings()
    PlannedLesson.objects.filter(pk=family['lesson1'].pk).update(
        scheduled_date=DAY + datetime.timedelta(days=1), scheduled_time=datetime.time(0, 0))
    assert reminders.send_parent_hour_before(at(DAY, 23, 0)) == 2
