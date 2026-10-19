"""
Тесты проверок здоровья планов. Спека:
docs/superpowers/specs/2026-08-05-plan-health-design.md §4.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.scheduling import health
from apps.scheduling.models import PlannedLesson

pytestmark = pytest.mark.django_db

_MARKER = '__health_test__'


@pytest.fixture(autouse=True)
def _cleanup_lessons(group_with_group):
    yield
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE submitted_by_token = %s', [_MARKER])


def _lesson(group_id: int, teacher_id: int, date: str, number, lesson_type='regular') -> int:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
            "VALUES (%s,%s,%s,%s,60,%s,NOW(),'__health_test__') RETURNING id",
            [group_id, teacher_id, date, number, lesson_type])
        return cur.fetchone()[0]


def _counts_for(group_id: int) -> dict:
    report = health.check_all()
    for row in report['groups']:
        if row['group_id'] == group_id:
            return row['counts']
    return {}


def test_healthy_group_not_reported(group_with_group):
    """Здоровая группа в отчёт не попадает."""
    gid, _tid = group_with_group
    assert _counts_for(gid) == {}


def test_detects_collision(group_with_group):
    """Две курсовые позиции на одну дату и время."""
    gid, _tid = group_with_group
    PlannedLesson.objects.filter(group_id=gid, seq=2).update(
        scheduled_date='2026-07-07')   # seq=1 уже там, время у всех 18:00
    assert _counts_for(gid).get('collision') == 1


def test_detects_done_in_future(group_with_group):
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2099-01-01', 1)
    PlannedLesson.objects.filter(group_id=gid, seq=1).update(
        status='done', fact_lesson_id=lesson_id, scheduled_date='2099-01-01')
    assert _counts_for(gid).get('done_in_future') == 1


def test_detects_date_mismatch(group_with_group):
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-06-30', 1)
    PlannedLesson.objects.filter(group_id=gid, seq=1).update(
        status='done', fact_lesson_id=lesson_id)   # позиция осталась на 07.07
    assert _counts_for(gid).get('date_mismatch') == 1


def test_detects_number_mismatch(group_with_group):
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-07-07', 3)
    PlannedLesson.objects.filter(group_id=gid, seq=1).update(
        status='done', fact_lesson_id=lesson_id)   # у позиции номер 1, у урока 3
    assert _counts_for(gid).get('number_mismatch') == 1


def test_detects_fact_without_position(group_with_group):
    gid, tid = group_with_group
    _lesson(gid, tid, '2026-07-07', 1)             # ни к одной позиции не привязан
    assert _counts_for(gid).get('fact_without_position') == 1


def test_detects_beyond_course(group_with_group):
    """Длина курса фикстуры — 4 урока; позиция с номером 9 сверх него."""
    gid, tid = group_with_group
    now = datetime.datetime(2026, 7, 1, 12, 0)
    PlannedLesson.objects.create(
        group_id=gid, seq=9, lesson_number=9, scheduled_date='2026-09-01',
        scheduled_time=datetime.time(18, 0), teacher_id=tid, status='pending',
        created_at=now, updated_at=now)
    assert _counts_for(gid).get('beyond_course') == 1


def test_detects_duplicate_dates(group_with_group):
    gid, tid = group_with_group
    _lesson(gid, tid, '2026-07-07', 1)
    _lesson(gid, tid, '2026-07-07', 2)
    assert _counts_for(gid).get('duplicate_dates') == 1


def test_check_all_does_not_loop_over_groups(group_with_group, django_assert_num_queries):
    """Один запрос на всю сводку — цикл по группам недопустим (134 группы на проде)."""
    with django_assert_num_queries(1):
        health.check_all()


def test_check_group_returns_rows(group_with_group):
    """По одной группе отдаём конкретные строки, а не счётчики."""
    gid, tid = group_with_group
    lesson_id = _lesson(gid, tid, '2026-06-30', 1)
    PlannedLesson.objects.filter(group_id=gid, seq=1).update(
        status='done', fact_lesson_id=lesson_id)

    report = health.check_group(gid)

    assert report['group_id'] == gid
    rows = report['findings']['date_mismatch']
    assert len(rows) == 1
    assert rows[0]['seq'] == 1
    assert rows[0]['scheduled_date'] == datetime.date(2026, 7, 7)
    assert rows[0]['fact_date'] == datetime.date(2026, 6, 30)


def test_check_group_missing_group(group_with_group):
    assert health.check_group(999_999_999) is None


def test_archived_group_only_with_include_inactive(group_with_group):
    """Архивные группы по умолчанию не проверяются (интерфейс смотрит живые);
    include_inactive=True — проверяет все (сверка импорта)."""
    from apps.groups.models import Group

    gid, _tid = group_with_group
    PlannedLesson.objects.filter(group_id=gid, seq=2).update(scheduled_date='2026-07-07')
    Group.objects.filter(pk=gid).update(active=False)
    assert _counts_for(gid) == {}
    rows = {r['group_id']: r['counts'] for r in health.check_all(include_inactive=True)['groups']}
    assert rows[gid].get('collision') == 1
