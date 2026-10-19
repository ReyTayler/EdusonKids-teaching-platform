"""
Тесты repository.cancel_lesson (шаг 5, новая модель): отменённая курсовая строка
уезжает в конец курса + перенумерация pending/overdue по дате. Заменяет прежнюю
модель (relay всего хвоста по каденции). Прямые вызовы функции репозитория (без
API-слоя) — group_with_group из conftest.py (4 pending вт: 07/14/21/28 июля 2026,
слот вторник 18:00).
"""
import datetime
from decimal import Decimal

import pytest

from apps.scheduling.models import PlannedLesson

pytestmark = pytest.mark.django_db


def test_cancel_moves_to_end_and_renumbers(group_with_group):
    from apps.scheduling import repository

    gid, tid = group_with_group  # 4 pending вт: 07,14,21,28 июля; слот вторник
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)  # 21.07
    repository.cancel_lesson(
        gid, from_date=datetime.date(2026, 7, 21),
        marker_time=datetime.time(18, 0), marker_teacher_id=tid, lesson_id=row3.id,
    )

    assert PlannedLesson.objects.filter(
        group_id=gid, status='cancelled', scheduled_date='2026-07-21', seq__isnull=True,
    ).exists()
    course = list(
        PlannedLesson.objects.filter(group_id=gid, seq__isnull=False)
        .order_by('scheduled_date')
    )
    assert [c.seq for c in course] == [1, 2, 3, 4]
    assert str(course[-1].scheduled_date) == '2026-08-04'
    assert [str(c.scheduled_date) for c in course] == [
        '2026-07-07', '2026-07-14', '2026-07-28', '2026-08-04',
    ]
    # Отменённая строка (row3, ранее seq=3) — теперь физически последняя (seq=4).
    moved = PlannedLesson.objects.get(id=row3.id)
    assert moved.seq == 4
    assert str(moved.scheduled_date) == '2026-08-04'


def test_cancel_clears_substitution_on_moved_row(group_with_group):
    from apps.scheduling import repository

    gid, tid = group_with_group
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)
    PlannedLesson.objects.filter(id=row3.id).update(substitute_teacher_id=tid)
    repository.cancel_lesson(
        gid, from_date=datetime.date(2026, 7, 21),
        marker_time=datetime.time(18, 0), marker_teacher_id=tid, lesson_id=row3.id,
    )
    moved = PlannedLesson.objects.get(id=row3.id)
    assert moved.substitute_teacher_id is None
    assert str(moved.scheduled_date) == '2026-08-04'


def test_cancel_preserves_done_rows_and_continues_numbering(group_with_group):
    """Проведённая (done) строка не трогается: остаётся на месте с прежним seq;
    перенумерация pending продолжает нумерацию с done.seq+1 / done.lesson_number+step."""
    from apps.scheduling import repository

    gid, tid = group_with_group
    row1 = PlannedLesson.objects.get(group_id=gid, seq=1)  # 07.07 — уже проведён
    PlannedLesson.objects.filter(id=row1.id).update(status='done')
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)  # 21.07 — отменяем

    repository.cancel_lesson(
        gid, from_date=datetime.date(2026, 7, 21),
        marker_time=datetime.time(18, 0), marker_teacher_id=tid, lesson_id=row3.id,
    )

    done = PlannedLesson.objects.get(id=row1.id)
    assert done.status == 'done'
    assert done.seq == 1
    assert str(done.scheduled_date) == '2026-07-07'   # done неподвижен

    pending = list(
        PlannedLesson.objects.filter(group_id=gid, status__in=('pending', 'overdue'))
        .order_by('scheduled_date')
    )
    # 14.07 (было seq2), 28.07 (было seq4), 04.08 (отменённая row3, уехала в конец).
    assert [str(p.scheduled_date) for p in pending] == ['2026-07-14', '2026-07-28', '2026-08-04']
    assert [p.seq for p in pending] == [2, 3, 4]
    assert [p.lesson_number for p in pending] == [Decimal('2'), Decimal('3'), Decimal('4')]


def test_cancel_marker_carries_marker_teacher_and_time(group_with_group):
    from apps.scheduling import repository

    gid, tid = group_with_group
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)
    repository.cancel_lesson(
        gid, from_date=datetime.date(2026, 7, 21),
        marker_time=datetime.time(19, 30), marker_teacher_id=tid, lesson_id=row3.id,
    )
    marker = PlannedLesson.objects.get(
        group_id=gid, status='cancelled', scheduled_date='2026-07-21')
    assert marker.seq is None
    assert marker.lesson_number is None
    assert marker.scheduled_time == datetime.time(19, 30)
    assert marker.teacher_id == tid


def test_cancel_rejects_non_course_row(group_with_group):
    """Отмена не-курсовой строки (seq=NULL) должна отклоняться — cancel_lesson
    защищает тот же инвариант, что и services.cancel на уровне API."""
    from apps.scheduling import repository

    gid, tid = group_with_group
    now = datetime.datetime(2026, 7, 1, 12, 0)
    marker = PlannedLesson.objects.create(
        group_id=gid, seq=None, lesson_number=None,
        scheduled_date=datetime.date(2026, 7, 22), scheduled_time=datetime.time(18, 0),
        teacher_id=tid, status='cancelled', created_at=now, updated_at=now,
    )
    with pytest.raises(ValueError):
        repository.cancel_lesson(
            gid, from_date=datetime.date(2026, 7, 22),
            marker_time=datetime.time(18, 0), marker_teacher_id=tid, lesson_id=marker.id,
        )


def test_cancel_no_open_slot_keeps_row_in_place_but_renumbers(group_with_group):
    """Нет открытого слота (слот закрыт) → шаг переезда пропускается (нельзя
    развернуть каденцию), но маркер вставляется и перенумерация всё равно проходит."""
    from apps.scheduling import repository
    from django.db import connection

    gid, tid = group_with_group
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE group_schedule_slots SET effective_to='2026-07-21' WHERE group_id=%s",
            [gid],
        )
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)
    repository.cancel_lesson(
        gid, from_date=datetime.date(2026, 7, 21),
        marker_time=datetime.time(18, 0), marker_teacher_id=tid, lesson_id=row3.id,
    )
    moved = PlannedLesson.objects.get(id=row3.id)
    # Не открытого слота — дата не изменилась.
    assert str(moved.scheduled_date) == '2026-07-21'
    # Перенумерация всё равно применилась (seq остаётся контигуозным по дате).
    course = list(
        PlannedLesson.objects.filter(group_id=gid, seq__isnull=False)
        .order_by('scheduled_date')
    )
    assert [c.seq for c in course] == [1, 2, 3, 4]
