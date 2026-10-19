"""
build_calendar() — сервисный тест (не API): groupId в occurrence-payload.
Нужен для ссылки «Открыть группу» в попапе admin-календаря (см.
docs/superpowers/specs/2026-07-13-admin-calendar-design.md).
"""
from __future__ import annotations

import datetime
import itertools

import pytest
from django.db import connection

from apps.scheduling import repository, services

D = datetime.date
W_FROM = D(2026, 6, 1)
W_TO = D(2026, 6, 30)


@pytest.mark.django_db
def test_occurrence_includes_group_id(sched_setup):
    s = sched_setup
    repository.generate_for_group(s['group_a'])

    cal = services.build_calendar(W_FROM, W_TO, teacher_id=s['teacher_a'])

    assert len(cal['occurrences']) > 0
    assert cal['occurrences'][0]['groupId'] == s['group_a']


@pytest.mark.django_db
def test_substitute_shows_in_substitute_calendar_on_its_date(sched_setup):
    """Занятие с заменой попадает в календарь ПОДМЕНЯЮЩЕГО (B) на свою дату, а не
    преподавателя контента (A); occurrence несёт teacherOverride = имя B."""
    s = sched_setup
    repository.generate_for_group(s['group_a'])
    target_date = D(2026, 6, 15)   # понедельник — курсовая строка группы A
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE planned_lessons SET substitute_teacher_id=%s "
            "WHERE group_id=%s AND scheduled_date=%s AND seq IS NOT NULL",
            [s['teacher_b'], s['group_a'], target_date],
        )

    # Календарь ПОДМЕНЯЮЩЕГО (B) на эту дату — строка есть, эффективный препод = B.
    cal_b = services.build_calendar(target_date, target_date, teacher_id=s['teacher_b'])
    b_rows = [o for o in cal_b['occurrences'] if o['groupId'] == s['group_a']]
    assert len(b_rows) == 1
    assert b_rows[0]['teacher'] == '__sched_B__'
    assert b_rows[0]['teacherOverride'] == '__sched_B__'

    # Календарь преподавателя КОНТЕНТА (A) на эту дату — строки с заменой нет.
    cal_a = services.build_calendar(target_date, target_date, teacher_id=s['teacher_a'])
    a_rows = [o for o in cal_a['occurrences']
              if o['groupId'] == s['group_a'] and o['date'] == '2026-06-15']
    assert a_rows == []


@pytest.mark.django_db
def test_substitute_equal_group_teacher_no_override_badge(sched_setup):
    """Замена на того же преподавателя, что ведёт группу, НЕ показывает бейдж «замена»."""
    s = sched_setup
    repository.generate_for_group(s['group_a'])
    target_date = D(2026, 6, 15)
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE planned_lessons SET substitute_teacher_id=%s "
            "WHERE group_id=%s AND scheduled_date=%s AND seq IS NOT NULL",
            [s['teacher_a'], s['group_a'], target_date],   # заместитель = препод группы A
        )
    cal = services.build_calendar(target_date, target_date, teacher_id=s['teacher_a'])
    rows = [o for o in cal['occurrences'] if o['groupId'] == s['group_a']]
    assert len(rows) == 1
    assert rows[0]['teacher'] == '__sched_A__'
    assert rows[0]['teacherOverride'] is None   # тот же препод — бейджа нет


_seq_counter = itertools.count(1)


def _insert_planned(group_id, teacher_id, date_str, time_str, status='pending', fact_lesson_id=None):
    # Уникальный seq на вставку: partial-unique (group, seq) WHERE seq IS NOT NULL
    # (planned_lessons_group_seq_key) запрещает две курсовые строки на одну позицию.
    seq = next(_seq_counter)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO planned_lessons "
            "(group_id, seq, lesson_number, scheduled_date, scheduled_time, teacher_id, "
            " status, fact_lesson_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()) RETURNING id",
            [group_id, seq, seq, date_str, time_str, teacher_id, status, fact_lesson_id],
        )
        return cur.fetchone()[0]


def test_unfilled_planned_lessons_filters_and_scope(sched_setup):
    s = sched_setup
    # overdue-кандидат (прошлое, pending, без факта) у преподавателя A
    _insert_planned(s['group_a'], s['teacher_a'], '2026-06-02', '10:00')
    # done — исключается хранимым статусом
    _insert_planned(s['group_a'], s['teacher_a'], '2026-06-03', '10:00', status='done')
    # cancelled — исключается
    _insert_planned(s['group_a'], s['teacher_a'], '2026-06-04', '10:00', status='cancelled')
    # у преподавателя B (своя группа) — не попадёт при фильтре по A
    _insert_planned(s['group_b'], s['teacher_b'], '2026-06-05', '12:00')

    today = datetime.date(2026, 7, 1)

    all_rows = repository.unfilled_planned_lessons(today)
    dates = {(r['group_pk'], r['scheduled_date'].isoformat()) for r in all_rows}
    assert (s['group_a'], '2026-06-02') in dates       # pending попал
    assert (s['group_b'], '2026-06-05') in dates       # оба преподавателя (school-wide)
    assert all(r['scheduled_date'].isoformat() not in ('2026-06-03', '2026-06-04')
               for r in all_rows if r['group_pk'] == s['group_a'])  # done/cancelled нет

    a_rows = repository.unfilled_planned_lessons(today, teacher_id=s['teacher_a'])
    a_groups = {r['group_pk'] for r in a_rows}
    assert s['group_a'] in a_groups and s['group_b'] not in a_groups  # фильтр по преподавателю


def test_unfilled_planned_lessons_skips_groups_without_students(sched_setup):
    """Группа без активных членств не отдаёт строк: и «ученика сняли», и «группа
    заведена, но никого не набрали» — заполнять там нечего."""
    s = sched_setup
    _insert_planned(s['group_a'], s['teacher_a'], '2026-06-02', '10:00')
    _insert_planned(s['group_b'], s['teacher_b'], '2026-06-05', '12:00')
    today = datetime.date(2026, 7, 1)

    rows = repository.unfilled_planned_lessons(today)
    assert any(r['group_pk'] == s['group_a'] for r in rows)

    with connection.cursor() as cur:
        cur.execute('UPDATE group_memberships SET active = false WHERE group_id = %s',
                    [s['group_a']])

    rows = repository.unfilled_planned_lessons(today)
    assert not any(r['group_pk'] == s['group_a'] for r in rows)
    assert any(r['group_pk'] == s['group_b'] for r in rows), 'соседняя группа не задета'


def test_unfilled_planned_lessons_excludes_future_and_filled(sched_setup):
    s = sched_setup
    _insert_planned(s['group_a'], s['teacher_a'], '2026-08-01', '10:00')
    today = datetime.date(2026, 7, 1)
    rows = repository.unfilled_planned_lessons(today)
    assert all(r['scheduled_date'] <= today for r in rows)
