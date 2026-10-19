"""
Команда reopen_stale_renewed_deals: возврат в воронку учеников, чья последняя
сделка закрыта как «Продлён» на рубеже цикла.
"""
from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage

pytestmark = pytest.mark.django_db


def _closed_renewed_deal(student_id: int, cycle_no: int) -> RenewalDeal:
    """Сделка, закрытая как «Продлён», — как её оставлял старый backfill."""
    pipe = RenewalPipeline.objects.get(is_default=True)
    won = RenewalStage.objects.get(pipeline=pipe, key='renewed')
    return RenewalDeal.objects.create(
        student_id=student_id, cycle_no=cycle_no, pipeline=pipe,
        stage=won, outcome_at=timezone.now())


@pytest.fixture
def student_on_boundary(make_student, make_direction, make_teacher, make_attendance):
    """Ученик ровно на рубеже цикла (4 урока) с закрытой сделкой «Продлён»."""
    sid = make_student('__reopen_test_student__')
    did = make_direction()
    tid = make_teacher()
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, "
            "created_at, lesson_number_offset) "
            "VALUES ('__reopen_group__', %s, %s, false, true, now(), 0) RETURNING id",
            [did, tid])
        gid = cur.fetchone()[0]
    make_attendance(sid, gid, tid, count=4)
    deal = _closed_renewed_deal(sid, cycle_no=1)
    yield sid, deal
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [gid])
        cur.execute('DELETE FROM groups WHERE id = %s', [gid])


def test_dry_run_changes_nothing(student_on_boundary):
    _sid, deal = student_on_boundary
    call_command('reopen_stale_renewed_deals')
    deal.refresh_from_db()
    assert deal.outcome_at is not None, 'без --apply сделка должна остаться закрытой'
    assert deal.stage.key == 'renewed'


def test_apply_reopens_into_awaiting_renewal(student_on_boundary):
    """Цикл отработан ровно — авто-правило ставит «Ждём продление»."""
    _sid, deal = student_on_boundary
    call_command('reopen_stale_renewed_deals', '--apply')
    deal.refresh_from_db()
    assert deal.outcome_at is None, 'сделка должна стать открытой'
    assert deal.stage.key == 'awaiting_renewal'


def test_students_filter_skips_others(student_on_boundary):
    """--students с чужим id: подходящий ученик не в списке — не трогаем."""
    _sid, deal = student_on_boundary
    call_command('reopen_stale_renewed_deals', '--students', '999999999', '--apply')
    deal.refresh_from_db()
    assert deal.outcome_at is not None, 'ученика вне --students трогать нельзя'


def test_students_filter_applies_to_listed(student_on_boundary):
    """--students со своим id: обрабатываем только его."""
    sid, deal = student_on_boundary
    call_command('reopen_stale_renewed_deals', '--students', str(sid), '--apply')
    deal.refresh_from_db()
    assert deal.outcome_at is None
    assert deal.stage.key == 'awaiting_renewal'


def test_student_with_open_deal_untouched(student_on_boundary, make_student):
    """Ученика, у которого уже есть открытая сделка, команда не трогает."""
    sid, deal = student_on_boundary
    pipe = RenewalPipeline.objects.get(is_default=True)
    open_stage = RenewalStage.objects.get(pipeline=pipe, key='awaiting_renewal')
    other = RenewalDeal.objects.create(
        student_id=sid, cycle_no=2, pipeline=pipe, stage=open_stage)
    try:
        call_command('reopen_stale_renewed_deals', '--apply')
        deal.refresh_from_db()
        assert deal.outcome_at is not None, 'закрытая сделка не трогается — воронка не пуста'
    finally:
        other.activities.all().delete()
        other.delete()
