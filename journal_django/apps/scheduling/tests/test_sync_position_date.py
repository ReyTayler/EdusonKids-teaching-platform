"""
Тесты инварианта «плановая дата курсовой позиции = дата её факта».
Спека: docs/superpowers/specs/2026-08-05-plan-health-design.md §2-3.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.scheduling.models import PlannedLesson
from apps.scheduling.repository import sync_position_date

pytestmark = pytest.mark.django_db

_MARKER = '__sync_pos_test__'


@pytest.fixture(autouse=True)
def _cleanup_lessons(group_with_group):
    """
    lessons.group_id — FK без ON DELETE CASCADE в БД (см. sched_setup в
    conftest.py). group_with_group сам lessons не чистит — тесты этого файла
    создают их через _lesson() напрямую, поэтому подчищаем сами и раньше:
    фикстура зависит от group_with_group, поэтому её finalizer выполнится
    ДО finalizer'а group_with_group (обратный порядок установки) — lessons
    уйдут до удаления группы, иначе DELETE FROM groups упадёт по FK при
    отложенной проверке constraints в конце теста.
    """
    yield
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE submitted_by_token = %s', [_MARKER])


def _lesson(group_id: int, teacher_id: int, date: str, number, lesson_type='regular') -> int:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
            "VALUES (%s,%s,%s,%s,60,%s,NOW(),'__sync_pos_test__') RETURNING id",
            [group_id, teacher_id, date, number, lesson_type])
        return cur.fetchone()[0]


def test_moves_position_to_fact_date(group_with_group):
    """Позиция стоит на 07.07, факт проведён 09.07 → позиция переезжает на 09.07."""
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-09', 1)
    pos = PlannedLesson.objects.get(group_id=gid, seq=1)
    pos.fact_lesson_id = lesson_id
    pos.status = 'done'
    pos.save(update_fields=['fact_lesson', 'status'])

    assert sync_position_date(lesson_id) is True

    pos.refresh_from_db()
    assert pos.scheduled_date == datetime.date(2026, 7, 9)


def test_idempotent_when_dates_already_match(group_with_group):
    """Даты совпадают → ничего не пишем, False."""
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-07', 1)
    pos = PlannedLesson.objects.get(group_id=gid, seq=1)
    pos.fact_lesson_id = lesson_id
    pos.status = 'done'
    pos.save(update_fields=['fact_lesson', 'status'])

    assert sync_position_date(lesson_id) is False


def test_clears_moved_from_date(group_with_group):
    """Метка разового переноса гасится (спека §3.2)."""
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-09', 1)
    pos = PlannedLesson.objects.get(group_id=gid, seq=1)
    pos.fact_lesson_id = lesson_id
    pos.status = 'done'
    pos.moved_from_date = datetime.date(2026, 7, 1)
    pos.save(update_fields=['fact_lesson', 'status', 'moved_from_date'])

    sync_position_date(lesson_id)

    pos.refresh_from_db()
    assert pos.moved_from_date is None


def test_ignores_system_lesson(group_with_group):
    """Доп.урок позиции курса не занимает → no-op, без исключения."""
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-09', 1, lesson_type='extra')
    assert sync_position_date(lesson_id) is False


def test_missing_lesson_is_noop(group_with_group):
    """Несуществующий урок не роняет вызов."""
    assert sync_position_date(999_999_999) is False


def test_fact_without_position_is_noop(group_with_group):
    """Факт есть, позиции за ним не закреплено → no-op."""
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-09', 1)
    assert sync_position_date(lesson_id) is False


def test_allows_landing_on_occupied_date(group_with_group):
    """Дата факта совпала с датой другой позиции — разрешено (спека §3.2):
    два реальных занятия в один день бывают."""
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-14', 1)
    pos = PlannedLesson.objects.get(group_id=gid, seq=1)   # стоит на 07.07
    pos.fact_lesson_id = lesson_id
    pos.status = 'done'
    pos.save(update_fields=['fact_lesson', 'status'])

    assert sync_position_date(lesson_id) is True

    pos.refresh_from_db()
    assert pos.scheduled_date == datetime.date(2026, 7, 14)
    # Позиция seq=2 как стояла на 14.07, так и стоит — коллизия допустима.
    assert PlannedLesson.objects.filter(
        group_id=gid, scheduled_date='2026-07-14').count() == 2


def test_attach_fact_sets_position_date(group_with_group):
    """Запись урока задним числом ставит позицию на дату урока (спека §3.2):
    именно так разъехались позиции 20-23 в ПИ337."""
    from apps.scheduling.repository import attach_fact

    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-06-30', 1)   # позиция стоит на 07.07
    pos = PlannedLesson.objects.get(group_id=gid, seq=1)

    attach_fact(pos.id, lesson_id)

    pos.refresh_from_db()
    assert pos.status == 'done'
    assert pos.fact_lesson_id == lesson_id
    assert pos.scheduled_date == datetime.date(2026, 6, 30)


def test_link_facts_sets_position_dates(group_with_group):
    """Пакетная привязка тоже ставит плановую дату позиции по факту."""
    from apps.scheduling.repository import link_facts

    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-06-30', 1)   # позиция seq=1 стоит на 07.07

    assert link_facts(gid) == 1

    pos = PlannedLesson.objects.get(group_id=gid, seq=1)
    assert pos.status == 'done'
    assert pos.fact_lesson_id == lesson_id
    assert pos.scheduled_date == datetime.date(2026, 6, 30)


def test_relink_fact_sets_position_date(group_with_group):
    """Смена номера урока переносит факт на позицию своего номера — и та
    встаёт на дату факта."""
    from apps.scheduling.repository import relink_fact

    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-06-30', 1)
    first = PlannedLesson.objects.get(group_id=gid, seq=1)
    first.fact_lesson_id = lesson_id
    first.status = 'done'
    first.save(update_fields=['fact_lesson', 'status'])

    with connection.cursor() as cur:
        cur.execute('UPDATE lessons SET lesson_number=2 WHERE id=%s', [lesson_id])

    assert relink_fact(lesson_id) is True

    first.refresh_from_db()
    assert first.fact_lesson_id is None
    assert first.status == 'pending'

    second = PlannedLesson.objects.get(group_id=gid, seq=2)
    assert second.fact_lesson_id == lesson_id
    assert second.scheduled_date == datetime.date(2026, 6, 30)


def test_update_lesson_date_moves_position(group_with_group):
    """Правка даты урока двигает плановую строку — прямая причина поломки ПИ337."""
    from apps.lessons.services import update_lesson
    from apps.scheduling.repository import attach_fact

    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-07', 1)
    pos = PlannedLesson.objects.get(group_id=gid, seq=1)
    attach_fact(pos.id, lesson_id)

    update_lesson(lesson_id, {'lesson_date': datetime.date(2026, 7, 2)})

    pos.refresh_from_db()
    assert pos.scheduled_date == datetime.date(2026, 7, 2)


def test_update_lesson_without_date_leaves_position(group_with_group):
    """Правка без смены даты позицию не трогает."""
    from apps.lessons.services import update_lesson
    from apps.scheduling.repository import attach_fact

    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-07', 1)
    pos = PlannedLesson.objects.get(group_id=gid, seq=1)
    attach_fact(pos.id, lesson_id)

    update_lesson(lesson_id, {'record_url': 'https://example.test/rec'})

    pos.refresh_from_db()
    assert pos.scheduled_date == datetime.date(2026, 7, 7)
