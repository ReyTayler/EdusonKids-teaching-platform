"""
Шаг 8 — «перекидывание» урока между календарями учителей при смене преподавателя.

Инвариант: GET /api/calendar скоупится по planned_lesson.teacher_id (препод
КОНКРЕТНОГО занятия). Значит смена преподавателя занятия (разово через reschedule
или навсегда через permanent_change) должна:
  - убрать занятие из календаря исходного преподавателя;
  - добавить его в календарь нового;
  - не затрагивать остальные занятия и проведённые (done).
"""
from __future__ import annotations

import datetime

import pytest

from apps.scheduling import repository, services
from apps.scheduling.models import PlannedLesson

D = datetime.date
W_FROM = D(2026, 6, 1)
W_TO = D(2026, 7, 31)


def _dates_in_calendar(teacher_id: int, group_name: str) -> set:
    """Множество дат занятий группы в календаре преподавателя за окно."""
    cal = services.build_calendar(W_FROM, W_TO, teacher_id=teacher_id)
    return {o['date'] for o in cal['occurrences'] if o['group'] == group_name}


@pytest.mark.django_db
def test_reschedule_teacher_moves_single_lesson_between_calendars(sched_setup):
    s = sched_setup
    repository.generate_for_group(s['group_a'])

    a_before = _dates_in_calendar(s['teacher_a'], s['group_a_name'])
    b_before = _dates_in_calendar(s['teacher_b'], s['group_a_name'])
    assert len(a_before) == 8          # весь курс у преподавателя A
    assert b_before == set()           # у B ничего из группы A

    # Разовый перенос первого занятия на другого преподавателя (B), новая дата.
    first = (
        PlannedLesson.objects
        .filter(group_id=s['group_a'], seq=1).values('id').first()
    )
    repository.reschedule_lesson(
        s['group_a'], first['id'],
        new_date=D(2026, 6, 2), new_time=datetime.time(10, 0),
        new_teacher_id=s['teacher_b'],
    )

    a_after = _dates_in_calendar(s['teacher_a'], s['group_a_name'])
    b_after = _dates_in_calendar(s['teacher_b'], s['group_a_name'])

    # Занятие ушло из календаря A (7 осталось) и появилось у B (новая дата).
    assert len(a_after) == 7
    assert D(2026, 6, 1).isoformat() not in a_after
    assert b_after == {D(2026, 6, 2).isoformat()}


@pytest.mark.django_db
def test_permanent_change_teacher_moves_tail_between_calendars(sched_setup):
    s = sched_setup
    repository.generate_for_group(s['group_a'])

    # Перенос навсегда с seq=3 (2026-06-15): тот же день/время (Пн 10:00),
    # новый преподаватель B.
    repository.permanent_change(
        s['group_a'], from_seq=3, effective_from=D(2026, 6, 15),
        new_slots=[{'day_of_week': 1, 'start_time': '10:00'}], new_teacher_id=s['teacher_b'],
    )

    a_after = _dates_in_calendar(s['teacher_a'], s['group_a_name'])
    b_after = _dates_in_calendar(s['teacher_b'], s['group_a_name'])

    # seq 1..2 остались у A (2), seq 3..8 переехали к B (6). Итого разбиение курса.
    assert len(a_after) == 2
    assert len(b_after) == 6
    assert a_after.isdisjoint(b_after)


@pytest.mark.django_db
def test_done_lesson_not_reassigned_by_permanent_change(sched_setup):
    s = sched_setup
    repository.generate_for_group(s['group_a'])

    # Помечаем seq=3 как проведённое (done) — оно не должно уехать к B.
    PlannedLesson.objects.filter(group_id=s['group_a'], seq=3).update(status='done')

    repository.permanent_change(
        s['group_a'], from_seq=3, effective_from=D(2026, 6, 15),
        new_slots=[{'day_of_week': 1, 'start_time': '10:00'}], new_teacher_id=s['teacher_b'],
    )

    done = PlannedLesson.objects.get(group_id=s['group_a'], seq=3)
    assert done.teacher_id == s['teacher_a']   # проведённое не перекинуто
    assert done.status == 'done'
