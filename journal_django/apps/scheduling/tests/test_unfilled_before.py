"""
has_unfilled_before — есть ли у группы незакрытое курсовое занятие с плановой
датой строго раньше отмечаемой (блокер отметки урока в teacher SPA).

Правило чисто по ДАТАМ, без учёта времени: два занятия одного дня друг друга не
блокируют (мультислот), но незакрытое занятие прошлого дня — блокирует. Поэтому
тестам не нужен ни monkeypatch времени, ни фиксированное «сейчас».

Фикстура group_with_group (conftest): 4 pending-строки на 07/14/21/28 июля 2026,
seq 1..4, 18:00.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.scheduling import repository

pytestmark = pytest.mark.django_db


def test_no_plan_returns_false(group_with_group):
    """Группа без плановых строк — блокировать нечем."""
    gid, _ = group_with_group
    with connection.cursor() as cur:
        cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [gid])
    assert repository.has_unfilled_before(gid, '2026-07-07') is False


def test_only_lesson_on_target_date_does_not_block(group_with_group):
    """Единственная незакрытая строка стоит на саму отмечаемую дату — не раньше
    неё, значит не долг."""
    gid, _ = group_with_group
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM planned_lessons WHERE group_id = %s AND '
            "scheduled_date <> '2026-07-07'",
            [gid],
        )
    assert repository.has_unfilled_before(gid, '2026-07-07') is False


def test_earlier_day_unfilled_blocks(group_with_group):
    """07.07 не отмечено, отмечают 14.07 → блок (факт 14.07 сядет на план 07.07)."""
    gid, _ = group_with_group
    assert repository.has_unfilled_before(gid, '2026-07-14') is True


def test_two_lessons_same_day_do_not_block(group_with_group):
    """Мультислот: два незакрытых занятия одного дня друг друга НЕ блокируют —
    оба факта получат дату этого дня, расхождения план/факт не возникнет."""
    gid, tid = group_with_group
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM planned_lessons WHERE group_id = %s AND '
            "scheduled_date <> '2026-07-07'",
            [gid],
        )
        # второй слот того же дня (10:00 к имеющемуся 18:00)
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, status, created_at, updated_at) '
            "VALUES (%s, 5, 5, '2026-07-07', '10:00', %s, 'pending', NOW(), NOW())",
            [gid, tid],
        )
    assert repository.has_unfilled_before(gid, '2026-07-07') is False


def test_future_rows_do_not_block(group_with_group):
    """Незакрытые строки ПОЗЖЕ отмечаемой даты не мешают ретро-отметке."""
    gid, _ = group_with_group
    assert repository.has_unfilled_before(gid, '2026-07-07') is False


def test_done_earlier_row_does_not_block(group_with_group):
    """Раннее занятие со status='done' долгом не считается."""
    gid, _ = group_with_group
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE planned_lessons SET status = 'done' "
            "WHERE group_id = %s AND scheduled_date = '2026-07-07'",
            [gid],
        )
    assert repository.has_unfilled_before(gid, '2026-07-14') is False


def test_cancelled_earlier_row_does_not_block(group_with_group):
    """Раннее занятие со status='cancelled' долгом не считается."""
    gid, _ = group_with_group
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE planned_lessons SET status = 'cancelled' "
            "WHERE group_id = %s AND scheduled_date = '2026-07-07'",
            [gid],
        )
    assert repository.has_unfilled_before(gid, '2026-07-14') is False


def test_earlier_row_with_linked_fact_does_not_block(group_with_group):
    """Ранняя строка со status='pending', но с привязанным фактом — не долг: урок
    за неё уже записан, статус просто не переставлен."""
    gid, tid = group_with_group
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
            "VALUES (%s, %s, '2026-07-07', 1, 60, 'regular', NOW(), 'test-unfilled') "
            'RETURNING id',
            [gid, tid],
        )
        fact_id = cur.fetchone()[0]
        cur.execute(
            'UPDATE planned_lessons SET fact_lesson_id = %s '
            "WHERE group_id = %s AND scheduled_date = '2026-07-07'",
            [fact_id, gid],
        )
    try:
        assert repository.has_unfilled_before(gid, '2026-07-14') is False
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE id = %s', [fact_id])


def test_non_course_earlier_row_does_not_block(group_with_group):
    """Ранняя строка без seq (маркер отмены/разовое занятие) долгом не считается."""
    gid, tid = group_with_group
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM planned_lessons WHERE group_id = %s AND '
            "scheduled_date IN ('2026-07-07','2026-07-14','2026-07-21','2026-07-28')",
            [gid],
        )
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, status, created_at, updated_at) '
            "VALUES (%s, NULL, NULL, '2026-07-01', '18:00', %s, 'pending', NOW(), NOW())",
            [gid, tid],
        )
    assert repository.has_unfilled_before(gid, '2026-07-07') is False


def test_other_group_rows_are_ignored(group_with_group, sched_setup):
    """Скоуп по group_id: ранние строки одной группы не влияют на другую."""
    gid, _ = group_with_group
    other_gid = sched_setup['group_a']
    # у group_with_group есть незакрытая строка 07.07 (раньше 14.07)
    assert repository.has_unfilled_before(gid, '2026-07-14') is True
    # у соседней группы этих строк нет → её счётчик их не видит
    assert repository.has_unfilled_before(other_gid, '2026-07-14') is False
