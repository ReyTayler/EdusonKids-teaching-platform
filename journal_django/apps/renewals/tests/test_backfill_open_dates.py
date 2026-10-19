"""Интеграционный тест недеструктивного бэкфилла реальных дат открытых сделок
(rebuild.backfill_open_dates): чинит дату авто-сделки из посещаемости, не трогая
стадию/ответственного; ручную decision-сделку не трогает.

Самодостаточная фикстура world создаёт teacher/direction/student/group/membership
и чистит всё в правильном порядке (важно из-за deferred-FK при check_constraints)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import connection

from apps.renewals import rebuild
from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage

pytestmark = pytest.mark.django_db


def _stage(pipe, key):
    return RenewalStage.objects.get(pipeline=pipe, key=key)


@pytest.fixture
def world(db):
    """Активный ученик в группе + фабрика посещений/оплат. Чистит в верном порядке."""
    ids = {'lessons': []}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, created_at) VALUES ('__bf_t__', now()) RETURNING id")
        ids['teacher'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, active, subscription_price) "
            "VALUES ('__bf_d__', true, '4000.00') RETURNING id")
        ids['direction'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES ('__bf_s__', now()) RETURNING id")
        ids['student'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
            "lesson_number_offset) "
            "VALUES ('__bf_g__', %s, %s, false, true, now(), 0) RETURNING id",
            [ids['direction'], ids['teacher']])
        ids['group'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true)", [ids['group'], ids['student']])

    class W:
        sid = ids['student']
        did = ids['direction']

        def lessons(self, count, start, duration=60):
            base = date.fromisoformat(start)
            with connection.cursor() as cur:
                for i in range(count):
                    cur.execute(
                        "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                        "lesson_duration_minutes, lesson_type, submitted_by_token) "
                        "VALUES (%s,%s,%s,%s,%s,'regular','__bf__') RETURNING id",
                        [ids['group'], ids['teacher'], base + timedelta(days=i), i + 1, duration])
                    lid = cur.fetchone()[0]
                    cur.execute("INSERT INTO lesson_attendance (lesson_id, student_id, present) "
                                "VALUES (%s,%s,true)", [lid, ids['student']])
                    ids['lessons'].append(lid)

        def payment(self, lessons=8):
            from apps.payments.models import Payment
            Payment.objects.create(
                student_id=ids['student'], direction_id=ids['direction'],
                subscriptions_count=None, lessons_count=lessons, kind='purchase',
                unit_price=0, total_amount='4000.00', paid_at='2026-02-01',
                created_at='2026-02-01T00:00:00Z')

    yield W()

    with connection.cursor() as cur:
        cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                    '(SELECT id FROM renewal_deal WHERE student_id = %s)', [ids['student']])
        cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [ids['student']])
        cur.execute('DELETE FROM payments WHERE student_id = %s', [ids['student']])
        for lid in ids['lessons']:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [ids['group']])
        cur.execute('DELETE FROM groups WHERE id = %s', [ids['group']])
        cur.execute('DELETE FROM directions WHERE id = %s', [ids['direction']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [ids['teacher']])
        cur.execute('DELETE FROM students WHERE id = %s', [ids['student']])


def test_backfill_fixes_auto_open_deal_date_from_attendance(world):
    pipe = RenewalPipeline.objects.get(is_default=True)
    # 6 уроков с 2026-02-10 → цикл 1 отработан, 2 урока во 2-й цикл, последний 15.02.
    world.lessons(6, '2026-02-10')
    world.payment(lessons=8)  # баланс ≥ 0 → без долга → расчётная стадия = lesson_2

    wrong = '2026-07-20T12:00:00+03:00'
    deal = RenewalDeal.objects.create(
        student_id=world.sid, cycle_no=2, pipeline=pipe, stage=_stage(pipe, 'lesson_2'))
    RenewalDeal.objects.filter(id=deal.id).update(stage_entered_at=wrong)

    res = rebuild.backfill_open_dates(dry_run=False)

    deal.refresh_from_db()
    assert deal.stage_entered_at.date() == date(2026, 2, 15)
    assert res['updated'] >= 1


def test_backfill_skips_manual_decision_stage(world):
    pipe = RenewalPipeline.objects.get(is_default=True)
    world.lessons(6, '2026-02-10')
    world.payment(lessons=8)

    thinking = _stage(pipe, 'thinking')
    assert thinking.is_auto is False
    deal = RenewalDeal.objects.create(
        student_id=world.sid, cycle_no=2, pipeline=pipe, stage=thinking)
    RenewalDeal.objects.filter(id=deal.id).update(stage_entered_at='2026-07-20T12:00:00+03:00')

    rebuild.backfill_open_dates(dry_run=False)

    deal.refresh_from_db()
    assert deal.stage_entered_at.date() == date(2026, 7, 20)  # ручную стадию не трогаем


def test_backfill_dry_run_writes_nothing(world):
    pipe = RenewalPipeline.objects.get(is_default=True)
    world.lessons(6, '2026-02-10')
    world.payment(lessons=8)
    deal = RenewalDeal.objects.create(
        student_id=world.sid, cycle_no=2, pipeline=pipe, stage=_stage(pipe, 'lesson_2'))
    RenewalDeal.objects.filter(id=deal.id).update(stage_entered_at='2026-07-20T12:00:00+03:00')

    res = rebuild.backfill_open_dates(dry_run=True)

    deal.refresh_from_db()
    assert deal.stage_entered_at.date() == date(2026, 7, 20)  # не изменилось
    assert res['dry_run'] is True and res['updated'] >= 1
