"""
Команда resync_plan_facts: снятие сдвига нумерации плана относительно занятий.

Сценарий ПИ316: в середине курса позиция осталась без факта (урок удалили), а все
последующие занятия сели на позицию с номером на единицу больше. Преподаватель
видел в календаре не тот номер, а пустая позиция висела непроведённой.
"""
from __future__ import annotations

import datetime

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.scheduling.models import PlannedLesson

pytestmark = pytest.mark.django_db


def _lesson(group_id: int, teacher_id: int, date: str, number) -> int:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
            "VALUES (%s,%s,%s,%s,90,'regular',NOW(),'__resync_test__') RETURNING id",
            [group_id, teacher_id, date, number])
        return cur.fetchone()[0]


def _position(group_id: int, teacher_id: int, seq: int, date: str, fact_id=None) -> PlannedLesson:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, status, fact_lesson_id, created_at, updated_at) '
            "VALUES (%s,%s,%s,%s,'10:00',%s,%s,%s,NOW(),NOW()) RETURNING id",
            [group_id, seq, seq, date, teacher_id,
             'done' if fact_id else 'pending', fact_id])
        return cur.fetchone()[0]


@pytest.fixture
def shifted_group(sched_setup):
    """Позиции 1..4; занятия №1,2,3 сидят на позициях 2,3,4 — сдвиг на единицу."""
    gid = sched_setup['group_a']
    tid = sched_setup['teacher_a']
    facts = [
        _lesson(gid, tid, '2026-06-08', 1),
        _lesson(gid, tid, '2026-06-15', 2),
        _lesson(gid, tid, '2026-06-22', 3),
    ]
    positions = [
        _position(gid, tid, 1, '2026-06-01'),                 # пустая — «дыра»
        _position(gid, tid, 2, '2026-06-08', facts[0]),       # занятие №1
        _position(gid, tid, 3, '2026-06-15', facts[1]),       # занятие №2
        _position(gid, tid, 4, '2026-06-22', facts[2]),       # занятие №3
    ]
    return {'group': gid, 'facts': facts, 'positions': positions}


def test_dry_run_changes_nothing(shifted_group):
    call_command('resync_plan_facts', '--group', str(shifted_group['group']))
    p2 = PlannedLesson.objects.get(id=shifted_group['positions'][1])
    assert p2.fact_lesson_id == shifted_group['facts'][0], 'без --apply правок быть не должно'


def test_apply_aligns_numbers_and_dates(shifted_group):
    """Каждое занятие встаёт на позицию своего номера, дата позиции = дате занятия."""
    call_command('resync_plan_facts', '--group', str(shifted_group['group']), '--apply')

    p1, p2, p3, p4 = (PlannedLesson.objects.get(id=i) for i in shifted_group['positions'])
    f1, f2, f3 = shifted_group['facts']

    assert (p1.fact_lesson_id, p1.status) == (f1, 'done')
    assert p1.scheduled_date == datetime.date(2026, 6, 8)
    assert (p2.fact_lesson_id, p2.status) == (f2, 'done')
    assert (p3.fact_lesson_id, p3.status) == (f3, 'done')
    # Последняя позиция освободилась — это ближайшее занятие впереди.
    assert (p4.fact_lesson_id, p4.status) == (None, 'pending')


def test_refuses_when_fact_has_no_matching_position(sched_setup):
    """Занятию с номером вне плана позиции не найти — команда не чинит наполовину."""
    gid, tid = sched_setup['group_a'], sched_setup['teacher_a']
    fact = _lesson(gid, tid, '2026-06-08', 99)
    _position(gid, tid, 1, '2026-06-01')
    with pytest.raises(CommandError):
        call_command('resync_plan_facts', '--group', str(gid), '--apply')
    assert PlannedLesson.objects.filter(fact_lesson_id=fact).count() == 0


def test_already_consistent_group_untouched(sched_setup):
    gid, tid = sched_setup['group_a'], sched_setup['teacher_a']
    fact = _lesson(gid, tid, '2026-06-01', 1)
    pid = _position(gid, tid, 1, '2026-06-01', fact)
    call_command('resync_plan_facts', '--group', str(gid), '--apply')
    p = PlannedLesson.objects.get(id=pid)
    assert (p.fact_lesson_id, p.status) == (fact, 'done')
