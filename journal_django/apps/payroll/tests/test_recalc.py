"""
Пересчёт начислений поверх БД (apps/payroll/recalc.py, спека §2–§3): создание и
обновление строк, ручные правки, идемпотентность, сироты отмен, блокировки,
число запросов. Данные выдуманные, мини-школа — conftest приложения (общая
eduson_test, всё внутри транзакции теста).
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.utils import timezone

from apps.core.utils.dates import msk_now
from apps.payroll.recalc import recalculate
from apps.payroll.tests.conftest import (
    db_lesson,
    db_marker,
    db_orphan,
    db_version,
    recalc_group,
)

UTC = datetime.timezone.utc
D = Decimal


def test_recalculate_creates_payroll_with_headcount(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1), db_school['direction'], D('500'))
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1,
                        present=(True, True, False), unpaid_skip=[db_school['students'][3]])
    stats = recalc_group(db_school)
    assert stats['lessons'] == 1
    assert stats['created'] == 1
    row = Payroll.objects.get(lesson=lesson)
    assert row.teacher_id == db_school['t1'].id
    # unpaid_skip вне total и вне present
    assert (row.total_students, row.present_count) == (3, 2)
    assert row.payment == row.calc_payment == D('500.00')
    assert row.penalty == row.calc_penalty == D('0.00')
    assert 'Тест Движок Направление' in row.rule_note
    assert row.is_manual is False


def test_recalculate_lesson_before_first_version(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1))
    early = db_lesson(db_school, datetime.date(2026, 5, 31), 1)
    late = db_lesson(db_school, datetime.date(2026, 6, 1), 2)
    recalc_group(db_school)
    assert Payroll.objects.get(lesson=early).payment == D('0.00')
    assert Payroll.objects.get(lesson=early).rule_note == 'Нет ставки на 31.05.2026'
    assert Payroll.objects.get(lesson=late).payment == D('600.00')


def test_recalculate_overwrites_stale_payment(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1))
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    Payroll.objects.create(lesson=lesson, teacher=db_school['t1'], total_students=0,
                           present_count=0, payment=D('1'), penalty=D('5'))
    stats = recalc_group(db_school)
    assert stats['created'] == 0
    assert stats['updated'] == 1
    row = Payroll.objects.get(lesson=lesson)
    assert (row.payment, row.penalty) == (D('600.00'), D('0.00'))
    assert (row.total_students, row.present_count) == (3, 2)


def test_recalculate_idempotent(db_school):
    db_version(datetime.date(2026, 6, 1), db_school['direction'], D('500'),
                penalty_enabled=True, penalty_mode='per_student', penalty_amount=D('40'))
    db_lesson(db_school, datetime.date(2026, 6, 10), 1,
               submitted_at=datetime.datetime(2026, 6, 10, 21, 30, tzinfo=UTC))
    db_lesson(db_school, datetime.date(2026, 6, 17), 2)
    db_marker(db_school, datetime.date(2026, 6, 24))
    first = recalc_group(db_school)
    assert first['created'] == 3
    assert first['changed'] == 3
    second = recalc_group(db_school)
    assert second['changed'] == 0
    assert second['created'] == second['updated'] == second['deleted'] == 0
    assert (second['lessons'], second['cancellations']) == (2, 1)


def test_recalculate_penalty_by_msk_day(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1), penalty_enabled=True,
                penalty_mode='per_student', penalty_amount=D('40'))
    late = db_lesson(db_school, datetime.date(2026, 6, 10), 1,
                      submitted_at=datetime.datetime(2026, 6, 10, 21, 30, tzinfo=UTC))
    in_time = db_lesson(db_school, datetime.date(2026, 6, 11), 2,
                         submitted_at=datetime.datetime(2026, 6, 11, 20, 59, tzinfo=UTC))
    admin = db_lesson(db_school, datetime.date(2026, 6, 12), 3, token='admin-imported',
                       submitted_at=datetime.datetime(2026, 6, 20, 9, 0, tzinfo=UTC))
    recalc_group(db_school)
    assert Payroll.objects.get(lesson=late).penalty == D('80.00')  # 2 присутствовали
    assert Payroll.objects.get(lesson=in_time).penalty == D('0.00')
    assert Payroll.objects.get(lesson=admin).penalty == D('0.00')


def test_recalculate_personal_rate_only_for_its_teacher(db_school):
    from apps.payroll.models import Payroll, PersonalRate
    db_version(datetime.date(2026, 6, 1))
    PersonalRate.objects.create(teacher=db_school['t2'], group=db_school['group'],
                                kind='fixed', value=D('900'))
    own = db_lesson(db_school, datetime.date(2026, 6, 10), 1, teacher=db_school['t2'])
    other = db_lesson(db_school, datetime.date(2026, 6, 17), 2, teacher=db_school['t1'])
    recalc_group(db_school)
    assert Payroll.objects.get(lesson=own).payment == D('900.00')
    assert Payroll.objects.get(lesson=other).payment == D('600.00')


def test_recalculate_keeps_manual_payment_updates_calc(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1))
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    Payroll.objects.create(lesson=lesson, teacher=db_school['t1'], total_students=0,
                           present_count=0, payment=D('1234.00'), penalty=D('7.00'),
                           is_manual=True, calc_payment=D('1'), calc_penalty=D('0'))
    stats = recalc_group(db_school)
    assert stats['updated'] == 1
    row = Payroll.objects.get(lesson=lesson)
    assert (row.payment, row.penalty, row.is_manual) == (D('1234.00'), D('7.00'), True)
    assert row.calc_payment == D('600.00')
    assert (row.total_students, row.present_count) == (3, 2)
    assert row.rule_note
    assert recalc_group(db_school)['changed'] == 0


def test_recalculate_teacher_change_drops_manual_payment(db_school):
    """Сменился преподаватель урока — ручная сумма прежнего к новому не переходит:
    правка снимается, payment/penalty = расчётным (как мягкое обновление импорта)."""
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1))
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    recalc_group(db_school)
    Payroll.objects.filter(lesson=lesson).update(payment=D('1234.00'), penalty=D('7.00'),
                                                 is_manual=True)
    lesson.teacher = db_school['t2']
    lesson.save()

    stats = recalc_group(db_school)

    assert stats['updated'] == 1
    row = Payroll.objects.get(lesson=lesson)
    assert row.teacher_id == db_school['t2'].id
    assert row.is_manual is False
    assert (row.payment, row.penalty) == (row.calc_payment, row.calc_penalty)
    assert row.payment == D('600.00')
    assert recalc_group(db_school)['changed'] == 0


def test_orphan_rebind_to_other_teacher_drops_manual(db_school):
    """Сирота с ручной суммой привязывается к маркеру другого преподавателя —
    сумма прежнего получателя не переносится, начисление расчётное."""
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    day = datetime.date(2026, 6, 10)
    orphan = db_orphan(db_school, day, payment='777')
    CancellationPay.objects.filter(id=orphan.id).update(is_manual=True)
    marker = db_marker(db_school, day, teacher=db_school['t2'])

    recalc_group(db_school)

    orphan.refresh_from_db()
    assert orphan.planned_lesson_id == marker.id
    assert orphan.teacher_id == db_school['t2'].id
    assert (orphan.payment, orphan.calc_payment, orphan.is_manual) == (
        D('300.00'), D('300.00'), False)


def test_recalculate_filters_combine(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1))
    a = db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    b = db_lesson(db_school, datetime.date(2026, 6, 17), 2)
    c = db_lesson(db_school, datetime.date(2026, 6, 24), 3)
    stats = recalc_group(db_school, date_from=datetime.date(2026, 6, 11),
                          date_to=datetime.date(2026, 6, 20))
    assert stats['lessons'] == 1
    assert set(Payroll.objects.filter(lesson__in=[a, b, c]).values_list('lesson_id', flat=True)) \
        == {b.id}
    # lesson_ids — только уроки, отмены не трогаются
    db_marker(db_school, datetime.date(2026, 6, 12))
    stats = recalculate(lesson_ids=[a.id])
    assert (stats['lessons'], stats['cancellations']) == (1, 0)
    # lesson_ids AND group_ids чужой группы → пусто
    assert recalculate(lesson_ids=[c.id], group_ids=[-1])['lessons'] == 0


def test_recalculate_cancellation_created_with_marker_teacher(db_school):
    from apps.payroll.models import CancellationPay, PersonalRate
    db_version(datetime.date(2026, 6, 1), db_school['direction'], D('500'))
    PersonalRate.objects.create(teacher=db_school['t2'], group=db_school['group'],
                                kind='plus_percent', value=D('20'))
    marker = db_marker(db_school, datetime.date(2026, 6, 10), teacher=db_school['t2'])
    stats = recalculate(marker_ids=[marker.id])
    assert (stats['lessons'], stats['cancellations'], stats['created']) == (0, 1, 1)
    pay = CancellationPay.objects.get(planned_lesson=marker)
    assert pay.teacher_id == db_school['t2'].id
    assert (pay.group_id, pay.lesson_date, pay.scheduled_time) == (
        db_school['group'].id, datetime.date(2026, 6, 10), datetime.time(15, 0))
    # 500 × 1.2 × 50 % = 300
    assert pay.payment == pay.calc_payment == D('300.00')
    assert pay.rule_note.endswith('Отмена: 50 % от 600 ₽ = 300 ₽')


def test_recalculate_orphans_past_kept_future_deleted(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    today = msk_now().date()
    past_orphan = db_orphan(db_school, today - datetime.timedelta(days=1))
    today_orphan = db_orphan(db_school, today)
    future_orphan = db_orphan(db_school, today + datetime.timedelta(days=7))
    stats = recalc_group(db_school)
    assert stats['deleted'] == 2
    assert CancellationPay.objects.filter(id=past_orphan.id).exists()
    assert not CancellationPay.objects.filter(id__in=[today_orphan.id, future_orphan.id]).exists()
    # Прошлая сирота не пересчитывается: деньги выплаченного месяца не меняются.
    past_orphan.refresh_from_db()
    assert past_orphan.payment == D('300.00')


def test_recalculate_orphan_rebinds_to_new_marker_keeping_manual(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    day = datetime.date(2026, 6, 10)
    old_marker = db_marker(db_school, day)
    recalc_group(db_school)
    pay = CancellationPay.objects.get(planned_lesson=old_marker)
    pay.payment = D('777.00')
    pay.is_manual = True
    pay.save()

    # Пересборка плана: маркер удалён (SET NULL), создан новый с тем же ключом
    # и тем же преподавателем — та же отмена, ручная правка сохраняется.
    old_marker.delete()
    pay.refresh_from_db()
    assert pay.planned_lesson_id is None
    new_marker = db_marker(db_school, day)

    stats = recalc_group(db_school)
    assert stats['created'] == 0
    assert stats['deleted'] == 0
    assert stats['updated'] == 1
    pay.refresh_from_db()
    assert pay.planned_lesson_id == new_marker.id
    assert pay.teacher_id == db_school['t1'].id
    assert (pay.payment, pay.is_manual) == (D('777.00'), True)
    assert pay.calc_payment == D('300.00')
    assert CancellationPay.objects.filter(group=db_school['group']).count() == 1


def test_orphan_rebinds_by_day_when_time_changed(db_school):
    """Время отмены сменилось (мягкое обновление): в группе на дату одна сирота и
    один маркер без начисления — это та же отмена. Иначе прошлая сирота осталась
    бы рядом с новым начислением — двойная оплата."""
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    day = datetime.date(2026, 6, 10)
    orphan = db_orphan(db_school, day, time=datetime.time(10, 0), payment='777')
    CancellationPay.objects.filter(id=orphan.id).update(is_manual=True)
    marker = db_marker(db_school, day, time=datetime.time(15, 0))

    stats = recalc_group(db_school)

    assert (stats['created'], stats['updated']) == (0, 1)
    orphan.refresh_from_db()
    assert orphan.planned_lesson_id == marker.id
    assert orphan.scheduled_time == datetime.time(15, 0)
    assert (orphan.payment, orphan.calc_payment, orphan.is_manual) == (D('777'), D('300.00'), True)
    assert CancellationPay.objects.filter(group=db_school['group']).count() == 1


def test_orphan_not_rebound_by_day_when_ambiguous(db_school):
    """Два маркера без начисления на дату — какой из них «та же» отмена, неизвестно:
    сирота остаётся (прошлая), маркеры получают свои начисления."""
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    day = datetime.date(2026, 6, 10)
    orphan = db_orphan(db_school, day, time=datetime.time(10, 0), payment='1')
    db_marker(db_school, day, time=datetime.time(15, 0))
    db_marker(db_school, day, time=datetime.time(17, 0))

    stats = recalc_group(db_school)

    assert stats['created'] == 2
    orphan.refresh_from_db()
    assert orphan.planned_lesson_id is None


def test_orphan_not_rebound_by_day_when_two_orphans(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    day = datetime.date(2026, 6, 10)
    first = db_orphan(db_school, day, time=datetime.time(10, 0), payment='1')
    second = db_orphan(db_school, day, time=datetime.time(11, 0), payment='2')
    marker = db_marker(db_school, day, time=datetime.time(15, 0))

    recalc_group(db_school)

    assert CancellationPay.objects.get(planned_lesson=marker).payment == D('300.00')
    assert set(CancellationPay.objects.filter(planned_lesson__isnull=True)
               .values_list('id', flat=True)) == {first.id, second.id}


def test_orphan_not_rebound_by_day_when_marker_already_paid(db_school):
    """Маркер дня уже со своим начислением — сирота другого времени к нему не липнет."""
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    day = datetime.date(2026, 6, 10)
    marker = db_marker(db_school, day, time=datetime.time(15, 0))
    recalc_group(db_school)
    orphan = db_orphan(db_school, day, time=datetime.time(10, 0), payment='1')

    stats = recalc_group(db_school)

    assert stats['changed'] == 0
    orphan.refresh_from_db()
    assert orphan.planned_lesson_id is None
    assert CancellationPay.objects.filter(planned_lesson=marker).count() == 1


def test_point_recalc_by_lesson_deletes_future_orphans_of_its_group(db_school):
    # reset_plan удалил маркеры (SET NULL) — следующий пересчёт, затронувший
    # группу хоть одним уроком, убирает будущие сироты; прошлые остаются.
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    today = msk_now().date()
    past_marker = db_marker(db_school, today - datetime.timedelta(days=3))
    future_marker = db_marker(db_school, today + datetime.timedelta(days=3))
    recalc_group(db_school)
    past_marker.delete()
    future_marker.delete()
    lesson = db_lesson(db_school, today - datetime.timedelta(days=1), 1)

    stats = recalculate(lesson_ids=[lesson.id])
    assert (stats['lessons'], stats['deleted']) == (1, 1)
    left = CancellationPay.objects.filter(group=db_school['group'])
    assert list(left.values_list('lesson_date', flat=True)) == [
        today - datetime.timedelta(days=3)]


def test_point_recalc_by_marker_deletes_future_orphans_of_its_group(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    future = msk_now().date() + datetime.timedelta(days=30)
    orphan = db_orphan(db_school, future, time=datetime.time(10, 0))
    marker = db_marker(db_school, future - datetime.timedelta(days=7))
    stats = recalculate(marker_ids=[marker.id])
    assert (stats['created'], stats['deleted']) == (1, 1)
    assert not CancellationPay.objects.filter(id=orphan.id).exists()


def test_future_orphan_kept_while_its_marker_is_outside_scope(db_school):
    # Маркер с тем же ключом существует, но в охват не попал (пересчёт по уроку):
    # сирота дождётся привязки, а не удаляется — иначе пропала бы ручная правка.
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    future = msk_now().date() + datetime.timedelta(days=5)
    orphan = db_orphan(db_school, future)
    db_marker(db_school, future)
    lesson = db_lesson(db_school, future - datetime.timedelta(days=10), 1)
    assert recalculate(lesson_ids=[lesson.id])['deleted'] == 0
    assert CancellationPay.objects.filter(id=orphan.id).exists()


def test_orphan_deletion_respects_date_to(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    today = msk_now().date()
    inside = db_orphan(db_school, today + datetime.timedelta(days=2))
    outside = db_orphan(db_school, today + datetime.timedelta(days=20))
    stats = recalc_group(db_school, date_from=today, date_to=today + datetime.timedelta(days=10))
    assert stats['deleted'] == 1
    assert not CancellationPay.objects.filter(id=inside.id).exists()
    assert CancellationPay.objects.filter(id=outside.id).exists()


def test_two_orphans_same_key_oldest_bound_extra_future_deleted(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    future = msk_now().date() + datetime.timedelta(days=4)
    first = db_orphan(db_school, future, payment='111')
    first.is_manual = True
    first.save()
    second = db_orphan(db_school, future, payment='222')
    marker = db_marker(db_school, future)
    stats = recalc_group(db_school)
    assert (stats['created'], stats['updated'], stats['deleted']) == (0, 1, 1)
    first.refresh_from_db()
    assert (first.planned_lesson_id, first.payment) == (marker.id, D('111.00'))
    assert not CancellationPay.objects.filter(id=second.id).exists()


def test_marker_without_teacher_skipped(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    marker = db_marker(db_school, datetime.date(2026, 6, 10), teacher=None)
    assert recalc_group(db_school)['cancellations'] == 0
    assert recalculate(marker_ids=[marker.id])['cancellations'] == 0
    assert not CancellationPay.objects.filter(group=db_school['group']).exists()


def test_marker_with_only_substitute_is_paid_to_substitute(db_school):
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2026, 6, 1))
    marker = db_marker(db_school, datetime.date(2026, 6, 10), teacher=None,
                       substitute=db_school['t2'])
    recalculate(marker_ids=[marker.id])
    assert CancellationPay.objects.get(planned_lesson=marker).teacher_id == db_school['t2'].id


def test_burned_lesson_not_paid(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1), penalty_enabled=True, penalty_amount=D('40'))
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1, lesson_type='burned',
                       submitted_at=datetime.datetime(2026, 6, 12, 9, 0, tzinfo=UTC))
    recalc_group(db_school)
    row = Payroll.objects.get(lesson=lesson)
    assert (row.payment, row.penalty) == (D('0.00'), D('0.00'))
    assert row.rule_note == 'Сгорание — не оплачивается'


def test_recalculate_ignores_non_marker_planned_lessons(db_school):
    from apps.payroll.models import CancellationPay
    from apps.scheduling.models import PlannedLesson
    db_version(datetime.date(2026, 6, 1))
    now = timezone.now()
    # Отменённая курсовая строка (seq задан) — не маркер; pending-строка тоже.
    PlannedLesson.objects.create(group=db_school['group'], seq=1, lesson_number=D('1'),
                                 scheduled_date=datetime.date(2026, 6, 10),
                                 scheduled_time=datetime.time(15, 0), teacher=db_school['t1'],
                                 status='cancelled', created_at=now, updated_at=now)
    PlannedLesson.objects.create(group=db_school['group'], seq=None,
                                 scheduled_date=datetime.date(2026, 6, 11),
                                 scheduled_time=datetime.time(15, 0), teacher=db_school['t1'],
                                 status='pending', created_at=now, updated_at=now)
    assert recalc_group(db_school)['cancellations'] == 0
    assert not CancellationPay.objects.filter(group=db_school['group']).exists()


def test_recalculate_query_count_does_not_grow(db_school, django_assert_max_num_queries):
    from apps.payroll.models import PersonalRate
    db_version(datetime.date(2026, 6, 1), db_school['direction'], D('500'))
    PersonalRate.objects.create(teacher=db_school['t1'], group=db_school['group'],
                                kind='plus_fixed', value=D('100'))
    for i in range(30):
        db_lesson(db_school, datetime.date(2026, 6, 1) + datetime.timedelta(days=i), i + 1)
        db_marker(db_school, datetime.date(2026, 6, 1) + datetime.timedelta(days=i))
    # Число запросов не зависит от числа уроков: загрузки + bulk-операции.
    with django_assert_max_num_queries(12):
        stats = recalc_group(db_school)
    assert stats['created'] == 60


def test_substitute_on_cancelled_marker_moves_pay(db_school):
    # Замену поставили на уже отменённое занятие: начисление уходит заменяющему
    # (эффективный преподаватель) по его персональной ставке. Ручная сумма
    # исходного преподавателя к заменяющему не переходит — правка снимается.
    from apps.payroll.models import CancellationPay, PersonalRate
    db_version(datetime.date(2026, 6, 1))
    PersonalRate.objects.create(teacher=db_school['t2'], group=db_school['group'],
                                kind='fixed', value=D('1000'))
    marker = db_marker(db_school, datetime.date(2026, 6, 10))
    recalculate(marker_ids=[marker.id])
    CancellationPay.objects.filter(planned_lesson=marker).update(
        payment=D('111.00'), is_manual=True)
    marker.substitute_teacher = db_school['t2']
    marker.save()
    stats = recalculate(marker_ids=[marker.id])
    assert stats['updated'] == 1
    pay = CancellationPay.objects.get(planned_lesson=marker)
    assert pay.teacher_id == db_school['t2'].id
    assert (pay.payment, pay.is_manual, pay.calc_payment) == (D('500.00'), False, D('500.00'))


def test_rule_note_has_no_personal_data(db_school):
    from apps.payroll.models import CancellationPay, Payroll, PersonalRate
    db_version(datetime.date(2026, 6, 1), db_school['direction'], D('500'),
               penalty_enabled=True, penalty_amount=D('40'))
    PersonalRate.objects.create(teacher=db_school['t1'], group=db_school['group'],
                                kind='plus_fixed', value=D('100'), note='Пометка про Иванова')
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1,
                        submitted_at=datetime.datetime(2026, 6, 12, 9, 0, tzinfo=UTC))
    db_marker(db_school, datetime.date(2026, 6, 17))
    recalc_group(db_school)
    notes = [Payroll.objects.get(lesson=lesson).rule_note,
             CancellationPay.objects.get(group=db_school['group']).rule_note]
    # Название направления — справочник школы, не ПДн; имён людей и примечаний нет.
    direction = db_school['direction'].name
    assert notes[0] == (f'Групповой урок · {direction} · ставка с 01.06.2026 — 500 ₽ · '
                        'Персональная ставка (все уроки): +100 ₽ к 500 ₽ = 600 ₽ · '
                        'Штраф за отчёт не в день урока: 40 ₽')
    for note in notes:
        for name in (db_school['t1'].name, db_school['t2'].name, 'Иванов',
                     *(st.full_name for st in db_school['students'])):
            assert name not in note


def test_lock_order_plan_before_money(db_school):
    # Порядок захвата «урок/план → деньги»: уроки, маркеры, затем Payroll и
    # CancellationPay; агрегат присутствия читается после лока уроков.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    db_version(datetime.date(2026, 6, 1))
    db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    db_marker(db_school, datetime.date(2026, 6, 11))
    recalc_group(db_school)
    with CaptureQueriesContext(connection) as ctx:
        recalc_group(db_school)
    sqls = [q['sql'] for q in ctx.captured_queries]

    def first(pred):
        return next(i for i, sql in enumerate(sqls) if pred(sql))

    locked = [sql for sql in sqls if 'FOR UPDATE' in sql]
    # Таблица основного FROM: подзапрос Exists в SELECT тоже содержит FROM, но в скобках.
    def main_table(sql):
        depth = 0
        for i, ch in enumerate(sql):
            depth += ch == '('
            depth -= ch == ')'
            if depth == 0 and sql.startswith(' FROM "', i):
                return sql[i + 6:sql.index('"', i + 7) + 1]

    tables = [main_table(sql) for sql in locked]
    assert tables == ['"lessons"', '"planned_lessons"', '"payroll"', '"payroll_cancellations"']
    attendance_aggregate = first(lambda sql: 'COUNT(' in sql and 'lesson_attendance' in sql)
    assert first(lambda sql: 'FOR UPDATE' in sql) < attendance_aggregate


def test_future_orphan_deleted_when_same_key_marker_has_no_teacher(db_school):
    # Маркер без преподавателя к сироте не привяжется никогда — не держит её.
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    future = msk_now().date() + datetime.timedelta(days=5)
    orphan = db_orphan(db_school, future)
    db_marker(db_school, future, teacher=None)
    lesson = db_lesson(db_school, future - datetime.timedelta(days=10), 1)
    assert recalculate(lesson_ids=[lesson.id])['deleted'] == 1
    assert not CancellationPay.objects.filter(id=orphan.id).exists()


def test_lesson_date_to_inclusive(db_school):
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1))
    on_edge = db_lesson(db_school, datetime.date(2026, 6, 20), 1)
    after = db_lesson(db_school, datetime.date(2026, 6, 21), 2)
    stats = recalc_group(db_school, date_from=datetime.date(2026, 6, 20),
                         date_to=datetime.date(2026, 6, 20))
    assert stats['lessons'] == 1
    assert Payroll.objects.filter(lesson=on_edge).exists()
    assert not Payroll.objects.filter(lesson=after).exists()


def test_full_recalc_cleans_future_orphans_in_all_groups(db_school):
    from apps.groups.models import Group
    from apps.payroll.models import CancellationPay
    db_version(datetime.date(2020, 1, 1))
    other = Group.objects.create(
        name='Тест Движок Группа 3', direction=db_school['direction'], teacher=db_school['t1'],
        is_individual=False, lesson_duration_minutes=90, active=True,
        created_at=db_school['group'].created_at)
    today = msk_now().date()
    mine = db_orphan(db_school, today + datetime.timedelta(days=3))
    theirs = db_orphan({**db_school, 'group': other}, today + datetime.timedelta(days=4))
    past = db_orphan({**db_school, 'group': other}, today - datetime.timedelta(days=4))
    recalculate()
    assert not CancellationPay.objects.filter(id__in=[mine.id, theirs.id]).exists()
    assert CancellationPay.objects.filter(id=past.id).exists()


# ── режим «только отмены» (операции расписания, задача 3б) ──────────────────

def test_cancellations_only_does_not_touch_lessons(db_school):
    from apps.payroll.models import CancellationPay, Payroll
    from apps.payroll.recalc import recalculate_cancellations
    db_version(datetime.date(2026, 6, 1))
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    marker = db_marker(db_school, datetime.date(2026, 6, 11))
    stats = recalculate_cancellations(group_ids=[db_school['group'].id])
    assert (stats['lessons'], stats['cancellations'], stats['created']) == (0, 1, 1)
    assert not Payroll.objects.filter(lesson=lesson).exists()
    assert CancellationPay.objects.get(planned_lesson=marker).payment == D('300.00')


def test_cancellations_only_cleans_future_orphans_keeps_past(db_school):
    from apps.payroll.models import CancellationPay
    from apps.payroll.recalc import recalculate_cancellations
    db_version(datetime.date(2020, 1, 1))
    today = msk_now().date()
    past = db_orphan(db_school, today - datetime.timedelta(days=2))
    future = db_orphan(db_school, today + datetime.timedelta(days=2))
    stats = recalculate_cancellations(group_ids=[db_school['group'].id], date_from=today)
    assert stats['deleted'] == 1
    assert CancellationPay.objects.filter(id=past.id).exists()
    assert not CancellationPay.objects.filter(id=future.id).exists()


def test_cancellations_only_never_locks_or_reads_lessons(db_school):
    # Операции расписания держат строки плана: лок уроков после них дал бы
    # обратный порядок к пересчёту по ставкам (уроки → план) — дедлок.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from apps.payroll.recalc import recalculate_cancellations
    db_version(datetime.date(2026, 6, 1))
    db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    marker = db_marker(db_school, datetime.date(2026, 6, 11))
    with CaptureQueriesContext(connection) as ctx:
        recalculate_cancellations(group_ids=[db_school['group'].id])
        recalculate_cancellations(marker_ids=[marker.id])
    sqls = [q['sql'] for q in ctx.captured_queries]
    assert not [sql for sql in sqls if '"lessons"' in sql or '"payroll"' in sql]
    locked = [sql for sql in sqls if 'FOR UPDATE' in sql]
    assert locked and all('"payroll_cancellations"' in sql or '"planned_lessons"' in sql
                          for sql in locked)
