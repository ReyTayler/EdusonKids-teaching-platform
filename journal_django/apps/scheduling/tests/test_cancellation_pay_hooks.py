"""
Начисления за отмены при операциях расписания (спека
2026-10-20-eduson-motivation-payroll-design, §3 «Поводы пересчёта»): отмена,
замена и перенос маркера, удаление маркеров пересборкой плана (reset_plan) и
сменой расписания (wipe_one_offs). Порядок блокировок «план → деньги».

group_with_group — conftest приложения (4 pending вт июля 2026, слот вт 18:00);
payroll_rates — корневой conftest (групповой 500, отмена 50 %). Данные выдуманные.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.utils.dates import msk_now
from apps.scheduling.models import PlannedLesson

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def plan_group(group_with_group, payroll_rates):
    """Группа с планом + действующие ставки (без них начисление — 0 «нет ставки»).
    Уроки тестов убираем до разрушения группы: её фикстура чистит сырым DELETE."""
    yield group_with_group
    gid = group_with_group[0]
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll_cancellations WHERE group_id = %s', [gid])
        cur.execute('DELETE FROM lesson_cancellations WHERE group_id = %s', [gid])
        cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [gid])
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [gid])


def _teacher(name):
    from apps.teachers.models import Teacher
    return Teacher.objects.create(name=name, created_at=timezone.now())


def _marker(gid, tid, day, time=datetime.time(18, 0)):
    now = timezone.now()
    return PlannedLesson.objects.create(
        group_id=gid, seq=None, lesson_number=None, scheduled_date=day, scheduled_time=time,
        teacher_id=tid, status='cancelled', created_at=now, updated_at=now)


def _cancel_row3(gid, tid):
    from apps.scheduling import repository
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)  # 21.07
    repository.cancel_lesson(gid, lesson_id=row3.id)
    return PlannedLesson.objects.get(group_id=gid, seq__isnull=True, status='cancelled')


def _paid_markers(gid, tid, *days):
    """Маркеры на даты + их начисления (как после отмены)."""
    from apps.payroll.recalc import recalculate_cancellations
    markers = [_marker(gid, tid, d) for d in days]
    recalculate_cancellations(group_ids=[gid])
    return markers


def test_cancel_creates_cancellation_pay(plan_group):
    from apps.payroll.models import CancellationPay
    gid, tid = plan_group
    marker = _cancel_row3(gid, tid)
    pay = CancellationPay.objects.get(planned_lesson=marker)
    assert (pay.teacher_id, pay.group_id) == (tid, gid)
    assert (pay.lesson_date, pay.scheduled_time) == (datetime.date(2026, 7, 21),
                                                     datetime.time(18, 0))
    # 50 % от групповой ставки 500.
    assert pay.payment == pay.calc_payment == D('250.00')
    assert pay.is_manual is False


def test_cancel_locks_plan_before_money_and_never_lessons(plan_group):
    gid, tid = plan_group
    with CaptureQueriesContext(connection) as ctx:
        _cancel_row3(gid, tid)
    sqls = [q['sql'] for q in ctx.captured_queries]
    locked = [sql for sql in sqls if 'FOR UPDATE' in sql]
    plan_locks = [i for i, sql in enumerate(locked) if 'FROM "planned_lessons"' in sql
                  and '"payroll_cancellations"' not in sql]
    money_locks = [i for i, sql in enumerate(locked) if '"payroll_cancellations"' in sql]
    assert plan_locks and money_locks
    assert max(plan_locks) < min(money_locks)
    assert not [sql for sql in locked if '"lessons"' in sql or 'FROM "payroll"' in sql]


def test_cancel_with_record_locks_plan_then_record_then_money(plan_group):
    """Отмена с причиной: запись LessonCancellation пишется после лока курса и
    до локов начисления — порядок «план → запись отмены → деньги» (план B4).
    Встречного порядка нет, значит нет и взаимной блокировки с пересчётом."""
    from apps.cancellations.models import LessonCancellation
    from apps.cancellations.rules import CancellationDraft
    from apps.scheduling import repository
    gid, tid = plan_group
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)  # 21.07
    draft = CancellationDraft(
        lesson_date=datetime.date(2026, 7, 21), side='teacher', reason_id=None,
        reason_name=None, reason_compensation=False, other_text='тест',
        notified_on=None, created_by_id=None)
    with CaptureQueriesContext(connection) as ctx:
        repository.cancel_lesson(gid, lesson_id=row3.id, record=draft)
    sqls = [q['sql'] for q in ctx.captured_queries]
    plan_locks = [i for i, sql in enumerate(sqls) if 'FOR UPDATE' in sql
                  and 'FROM "planned_lessons"' in sql and '"payroll_cancellations"' not in sql
                  and '"lesson_cancellations"' not in sql]
    record_ops = [i for i, sql in enumerate(sqls) if '"lesson_cancellations"' in sql
                  and ('INSERT' in sql or 'FOR UPDATE' in sql)]
    money_locks = [i for i, sql in enumerate(sqls) if 'FOR UPDATE' in sql
                   and '"payroll_cancellations"' in sql]
    assert plan_locks and record_ops and money_locks
    assert min(plan_locks) < min(record_ops)
    assert max(record_ops) < min(money_locks)
    assert not [sql for sql in sqls if 'FOR UPDATE' in sql
                and ('"lessons"' in sql or 'FROM "payroll"' in sql)]
    assert LessonCancellation.objects.filter(group_id=gid).count() == 1


def test_substitute_on_cancelled_marker_moves_pay_dropping_manual(plan_group):
    """Замена на отменённом занятии: начисление уходит заменяющему, а ручная сумма
    исходного преподавателя к нему не переходит — правка снимается, сумма расчётная."""
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, tid = plan_group
    marker = _cancel_row3(gid, tid)
    CancellationPay.objects.filter(planned_lesson=marker).update(
        payment=D('400.00'), is_manual=True)
    substitute = _teacher('Тест Отмены Замена')

    repository.change_teacher(gid, marker.id, substitute.id)

    pay = CancellationPay.objects.get(planned_lesson=marker)
    assert pay.teacher_id == substitute.id
    assert (pay.payment, pay.is_manual, pay.calc_payment) == (D('250.00'), False, D('250.00'))


def test_reschedule_marker_moves_pay_date(plan_group):
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, tid = plan_group
    marker = _cancel_row3(gid, tid)
    repository.reschedule_lesson(gid, marker.id, datetime.date(2026, 7, 23),
                                 datetime.time(11, 0), None)
    pay = CancellationPay.objects.get(planned_lesson=marker)
    assert (pay.lesson_date, pay.scheduled_time) == (datetime.date(2026, 7, 23),
                                                     datetime.time(11, 0))


def test_reset_plan_keeps_past_pay_deletes_future(plan_group):
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, tid = plan_group
    today = msk_now().date()
    past, future = _paid_markers(gid, tid, today - datetime.timedelta(days=3),
                                 today + datetime.timedelta(days=3))
    past_pay = CancellationPay.objects.get(planned_lesson=past)

    repository.reset_plan(gid)

    assert not PlannedLesson.objects.filter(group_id=gid).exists()
    left = list(CancellationPay.objects.filter(group_id=gid))
    assert [p.id for p in left] == [past_pay.id]
    assert left[0].planned_lesson_id is None
    assert left[0].payment == D('250.00')


def test_reset_plan_locks_plan_before_unlinking_money(plan_group):
    # Коллектор Django обнуляет ссылку начисления раньше DELETE плана — строки
    # плана к этому моменту уже должны быть залочены (порядок «план → деньги»).
    from apps.scheduling import repository
    gid, tid = plan_group
    _paid_markers(gid, tid, msk_now().date() - datetime.timedelta(days=3))
    with CaptureQueriesContext(connection) as ctx:
        repository.reset_plan(gid)
    sqls = [q['sql'] for q in ctx.captured_queries]
    plan_lock = next(i for i, sql in enumerate(sqls)
                     if 'FOR UPDATE' in sql and 'FROM "planned_lessons"' in sql)
    unlink = next(i for i, sql in enumerate(sqls)
                  if sql.startswith('UPDATE "payroll_cancellations"'))
    assert plan_lock < unlink


def test_wipe_one_offs_keeps_past_pay_deletes_future(plan_group):
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, tid = plan_group
    today = msk_now().date()
    past, future = _paid_markers(gid, tid, today - datetime.timedelta(days=3),
                                 today + datetime.timedelta(days=3))
    past_pay = CancellationPay.objects.get(planned_lesson=past)

    repository.wipe_one_offs(gid, date_from=today - datetime.timedelta(days=10))

    assert not PlannedLesson.objects.filter(group_id=gid, status='cancelled').exists()
    left = list(CancellationPay.objects.filter(group_id=gid))
    assert [p.id for p in left] == [past_pay.id]
    assert left[0].planned_lesson_id is None


def test_wipe_one_offs_locks_markers_before_unlinking_money(plan_group):
    from apps.scheduling import repository
    gid, tid = plan_group
    today = msk_now().date()
    _paid_markers(gid, tid, today + datetime.timedelta(days=3))
    with CaptureQueriesContext(connection) as ctx:
        repository.wipe_one_offs(gid, date_from=today)
    sqls = [q['sql'] for q in ctx.captured_queries]
    plan_lock = next(i for i, sql in enumerate(sqls)
                     if 'FOR UPDATE' in sql and 'FROM "planned_lessons"' in sql)
    unlink = next(i for i, sql in enumerate(sqls)
                  if sql.startswith('UPDATE "payroll_cancellations"'))
    assert plan_lock < unlink


# ── ревью задачи 3б ─────────────────────────────────────────────────────────

def _lesson(gid, tid, day, number):
    from apps.lessons.models import Lesson
    return Lesson.objects.create(
        group_id=gid, teacher_id=tid, lesson_date=day, lesson_number=D(str(number)),
        lesson_duration_minutes=60, lesson_type='regular',
        submitted_at=timezone.now(), submitted_by_token='acct:1')


def test_link_facts_never_binds_lesson_to_cancel_marker(plan_group):
    # Урок на дату отменённого занятия (номер не совпал ни с одной позицией):
    # проход «по дате» не должен сделать маркер проведённым — иначе платили бы
    # и за отмену, и за урок.
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, tid = plan_group
    marker = _cancel_row3(gid, tid)
    lesson = _lesson(gid, tid, datetime.date(2026, 7, 21), 99)

    repository.link_facts(gid)

    marker.refresh_from_db()
    assert (marker.status, marker.fact_lesson_id) == ('cancelled', None)
    assert not PlannedLesson.objects.filter(fact_lesson_id=lesson.id).exists()
    assert CancellationPay.objects.get(planned_lesson=marker).payment == D('250.00')


def test_cancel_marker_without_teacher_creates_no_pay(plan_group):
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, _ = plan_group
    row3 = PlannedLesson.objects.get(group_id=gid, seq=3)
    # Занятие без преподавателя (В2): маркер наследует None из строки.
    PlannedLesson.objects.filter(id=row3.id).update(teacher_id=None, substitute_teacher_id=None)
    repository.cancel_lesson(gid, lesson_id=row3.id)
    assert PlannedLesson.objects.filter(group_id=gid, status='cancelled',
                                        teacher_id__isnull=True).exists()
    assert not CancellationPay.objects.filter(group_id=gid).exists()


def test_course_row_change_teacher_and_reschedule_do_not_touch_money(plan_group):
    from apps.scheduling import repository
    gid, tid = plan_group
    _cancel_row3(gid, tid)
    substitute = _teacher('Тест Отмены Замена курса')
    row1 = PlannedLesson.objects.get(group_id=gid, seq=1)
    with CaptureQueriesContext(connection) as ctx:
        repository.change_teacher(gid, row1.id, substitute.id)
        repository.reschedule_lesson(gid, row1.id, datetime.date(2026, 7, 8),
                                     datetime.time(18, 0), None)
    assert not [q['sql'] for q in ctx.captured_queries
                if '"payroll_cancellations"' in q['sql']]


def test_reset_plan_leaves_other_groups_orphans(plan_group):
    # Всё чужое — своё, через ORM: фикстура группы сносит своё направление и
    # преподавателя сырым DELETE раньше отката транзакции теста.
    from apps.directions.models import Direction
    from apps.groups.models import Group
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, tid = plan_group
    direction = Direction.objects.create(name='Тест Отмены Чужое направление',
                                         total_lessons=8)
    other_teacher = _teacher('Тест Отмены Чужой')
    other = Group.objects.create(
        name='Тест Отмены Чужая группа', direction=direction,
        teacher=other_teacher, is_individual=False, lesson_duration_minutes=60,
        active=True, created_at=timezone.now())
    future = msk_now().date() + datetime.timedelta(days=5)
    theirs = CancellationPay.objects.create(
        teacher=other_teacher, group=other, lesson_date=future,
        scheduled_time=datetime.time(18, 0), payment=D('250'), calc_payment=D('250'))

    repository.reset_plan(gid)

    assert CancellationPay.objects.filter(id=theirs.id).exists()


def test_permanent_change_wipes_future_markers_and_their_pay(plan_group):
    # «Изменить расписание» целиком: хвост → wipe_one_offs → маркеры → деньги.
    # Маркер до effective_from живёт со своим начислением, после — удаляется с ним.
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid, tid = plan_group
    today = msk_now().date()
    past, future = _paid_markers(gid, tid, today - datetime.timedelta(days=3),
                                 today + datetime.timedelta(days=3))

    with CaptureQueriesContext(connection) as ctx:
        repository.permanent_change(
            gid, from_seq=1, effective_from=today,
            new_slots=[{'day_of_week': 4, 'start_time': '17:00'}])

    assert not PlannedLesson.objects.filter(id=future.id).exists()
    assert list(CancellationPay.objects.filter(group_id=gid)
                .values_list('planned_lesson_id', flat=True)) == [past.id]
    locked = [q['sql'] for q in ctx.captured_queries if 'FOR UPDATE' in q['sql']]
    plan_locks = [i for i, sql in enumerate(locked) if 'FROM "planned_lessons"' in sql
                  and '"payroll_cancellations"' not in sql]
    money_locks = [i for i, sql in enumerate(locked) if '"payroll_cancellations"' in sql]
    assert plan_locks and money_locks and max(plan_locks) < min(money_locks)
    assert not [sql for sql in locked if 'FROM "lessons"' in sql or 'FROM "payroll"' in sql]
