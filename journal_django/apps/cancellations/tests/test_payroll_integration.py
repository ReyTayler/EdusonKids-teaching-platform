"""Начисление за отмену учитывает запись об отмене (B5 плана, спека §4).

Маркер с записью LessonCancellation: compensated=False → 0 «Без компенсации: …»,
compensated=True → сумма «Мотивации» с причиной в пояснении. Маркер без записи
(отмены до фичи, импорт) — платится, как раньше. Запись переживает маркер и
привязывается движком к новому: по FK, затем по ключу (группа + дата + время),
затем по дню. Порядок блокировок: план → записи отмен → деньги.

payroll_rates — корневой conftest: групповой урок 500, отмена 50 % → 250.
Данные выдуманные; группа — 4 pending-занятия по вторникам июля 2026, слот вт 18:00.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.utils.dates import msk_now

pytestmark = pytest.mark.django_db

D = Decimal
DAYS = ['2026-07-07', '2026-07-14', '2026-07-21', '2026-07-28']
T18 = datetime.time(18, 0)
OTHER_WITH_NAME = 'Мама Иванова Пети попросила перенести'


@pytest.fixture
def pgroup(db, payroll_rates):
    from apps.cancellations.models import LessonCancellation
    from apps.directions.models import Direction
    from apps.groups.models import Group, GroupScheduleSlot
    from apps.scheduling.models import PlannedLesson
    from apps.teachers.models import Teacher

    now = timezone.now()
    direction = Direction.objects.create(name='Тест Компенсация Направление', total_lessons=4)
    teacher = Teacher.objects.create(name='Тест Компенсация Преподаватель', created_at=now)
    group = Group.objects.create(
        name='Тест Компенсация Группа', direction=direction, teacher=teacher,
        is_individual=False, lesson_duration_minutes=60, lessons_per_week=1,
        active=True, lesson_number_offset=0, created_at=now,
    )
    GroupScheduleSlot.objects.create(group=group, day_of_week=2, start_time=T18,
                                     effective_from=datetime.date(2026, 7, 1))
    rows = [
        PlannedLesson.objects.create(
            group=group, seq=i, lesson_number=i, scheduled_date=d, scheduled_time=T18,
            teacher=teacher, status='pending', created_at=now, updated_at=now)
        for i, d in enumerate(DAYS, start=1)
    ]
    yield {'group': group, 'gid': group.id, 'tid': teacher.id, 'rows': rows}
    # Запись ссылается на учётку автора, которую клиентская фикстура удаляет
    # сырым DELETE (отложенный FK сверяется в конце теста) — чистим до неё.
    LessonCancellation.objects.filter(group=group).delete()


def _cancel(client, pgroup, *, row=2, **body):
    from apps.scheduling.models import PlannedLesson
    target = pgroup['rows'][row]
    payload = {'lesson_date': DAYS[row], **body}
    resp = client.post(f"/api/admin/groups/{pgroup['gid']}/plan/{target.id}/cancel",
                       payload, format='json')
    assert resp.status_code == 200, resp.content
    return PlannedLesson.objects.get(group_id=pgroup['gid'], seq__isnull=True,
                                     scheduled_date=DAYS[row])


def _pay(marker):
    from apps.payroll.models import CancellationPay
    return CancellationPay.objects.get(planned_lesson=marker)


def _record(make_record, pgroup, day, *, marker=None, time=T18, compensated=False,
            reason_text='Заболел', side='client'):
    kwargs = {'group_id': pgroup['gid'], 'teacher_id': pgroup['tid'], 'lesson_date': day,
              'scheduled_time': time, 'planned_lesson': marker, 'compensated': compensated,
              'reason_text': reason_text, 'side': side}
    if side == 'teacher':
        kwargs['notified_on'] = None
    kwargs['reason'] = _reason_for(side)
    return make_record(**kwargs)


def _reason_for(side):
    from apps.cancellations.models import CancellationReason
    reason, _ = CancellationReason.objects.get_or_create(
        side=side, name='Заболел', deleted_at=None,
        defaults={'compensation': True, 'position': 0})
    return reason


def _drop_markers(gid, *markers):
    """Удалить маркеры так, как это делают reset_plan / wipe_one_offs: под локом плана."""
    from apps.scheduling.models import PlannedLesson
    from apps.scheduling.repository import lock_plan_rows
    qs = PlannedLesson.objects.filter(group_id=gid, id__in=[m.id for m in markers])
    lock_plan_rows(qs)
    qs.delete()


def _recalc(**scope):
    from apps.payroll.recalc import recalculate_cancellations
    return recalculate_cancellations(**scope)


# ---------------------------------------------------------------------------
# Решение из записи
# ---------------------------------------------------------------------------

def test_cancel_without_compensation_pays_zero_with_reason(manager_client, pgroup,
                                                           make_reason):
    reason = make_reason(side='client', name='Заболел', compensation=True)
    # Клиент предупредил накануне → по регламенту компенсации нет.
    marker = _cancel(manager_client, pgroup, side='client', reason_id=reason.id,
                     notified_on='2026-07-20')
    pay = _pay(marker)
    assert (pay.payment, pay.calc_payment, pay.is_manual) == (D('0.00'), D('0.00'), False)
    assert pay.rule_note == 'Без компенсации: Клиент — Заболел'


def test_cancel_with_compensation_pays_motivation_with_reason(manager_client, pgroup,
                                                              make_reason):
    reason = make_reason(side='teacher', name='Болезнь преподавателя', compensation=True)
    marker = _cancel(manager_client, pgroup, side='teacher', reason_id=reason.id)
    pay = _pay(marker)
    assert pay.payment == pay.calc_payment == D('250.00')
    assert pay.rule_note.startswith('Преподаватель — Болезнь преподавателя · ')
    assert pay.rule_note.endswith('Отмена: 50 % от 500 ₽ = 250 ₽')


def test_other_reason_text_never_in_rule_note(manager_client, pgroup):
    """Свободный текст «Другой» может содержать ПДн (имя клиента), а пояснение
    начисления видит преподаватель в кабинете — в rule_note только «Другая причина»."""
    marker = _cancel(manager_client, pgroup, side='teacher', other_text=OTHER_WITH_NAME)
    pay = _pay(marker)
    assert pay.payment == D('0.00')
    assert pay.rule_note == 'Без компенсации: Преподаватель — Другая причина'
    assert 'Иванов' not in pay.rule_note


def test_marker_without_record_paid_as_before(pgroup, make_marker):
    marker = make_marker(pgroup['gid'], pgroup['tid'], datetime.date(2026, 7, 21), T18)
    _recalc(group_ids=[pgroup['gid']])
    pay = _pay(marker)
    assert pay.payment == D('250.00')
    assert pay.rule_note.startswith('Групповой урок · ')
    assert pay.rule_note.endswith('Отмена: 50 % от 500 ₽ = 250 ₽')
    assert 'компенсац' not in pay.rule_note.lower()


def test_manual_payment_survives_recalc(manager_client, superadmin_client, pgroup,
                                        make_reason):
    reason = make_reason(side='client', compensation=False)
    marker = _cancel(manager_client, pgroup, side='client', reason_id=reason.id,
                     notified_on='2026-07-21')
    pay = _pay(marker)
    resp = superadmin_client.patch(f'/api/admin/payroll/cancellations/{pay.id}',
                                   {'payment': '300.00'}, format='json')
    assert resp.status_code == 200, resp.content

    _recalc(group_ids=[pgroup['gid']])

    pay.refresh_from_db()
    assert (pay.payment, pay.is_manual, pay.calc_payment) == (D('300.00'), True, D('0.00'))
    assert pay.rule_note == 'Без компенсации: Клиент — Заболел'


# ---------------------------------------------------------------------------
# Переживание маркера: перепривязка записи
# ---------------------------------------------------------------------------

def test_record_rebinds_by_key_without_history_events(pgroup, make_marker, make_record):
    from apps.cancellations.models import LessonCancellationEvent
    day = datetime.date(2026, 7, 21)
    m1 = make_marker(pgroup['gid'], pgroup['tid'], day, T18)
    rec = _record(make_record, pgroup, day, marker=m1, compensated=False)
    _recalc(marker_ids=[m1.id])
    _drop_markers(pgroup['gid'], m1)
    rec.refresh_from_db()
    assert rec.planned_lesson_id is None

    m2 = make_marker(pgroup['gid'], pgroup['tid'], day, T18)
    _recalc(marker_ids=[m2.id])

    rec.refresh_from_db()
    assert rec.planned_lesson_id == m2.id
    pay = _pay(m2)
    assert (pay.payment, pay.rule_note) == (D('0.00'), 'Без компенсации: Клиент — Заболел')
    # planned_lesson исключён из журнала: перепривязка — техника, не правка (В4).
    assert list(LessonCancellationEvent.objects.filter(pgh_obj_id=rec.id)
                .values_list('pgh_label', flat=True)) == ['insert']


def _orphaned(pgroup, make_marker, make_record, day, time=T18, *, compensated=False,
              reason_text='Заболел'):
    """Отмена с записью и начислением, чей маркер затем удалён (как reset_plan /
    wipe_one_offs / мягкое обновление): пара сирот — запись и CancellationPay с
    одним исходным ключом. Дата прошлая — сирота-начисление переживает пересчёты."""
    marker = make_marker(pgroup['gid'], pgroup['tid'], day, time)
    rec = _record(make_record, pgroup, day, marker=marker, time=time,
                  compensated=compensated, reason_text=reason_text)
    _recalc(marker_ids=[marker.id])
    pay = _pay(marker)
    _drop_markers(pgroup['gid'], marker)
    return rec, pay


def test_record_follows_pay_bound_by_day(pgroup, make_marker, make_record):
    """Время отмены сменилось: начисление-сирота привязывается по дню, запись идёт
    за ним по исходному ключу сироты — решение «без компенсации» сохраняется."""
    day = datetime.date(2026, 7, 21)
    rec, pay = _orphaned(pgroup, make_marker, make_record, day)
    m2 = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(11, 0))
    _recalc(group_ids=[pgroup['gid']])
    rec.refresh_from_db()
    pay.refresh_from_db()
    assert (rec.planned_lesson_id, pay.planned_lesson_id) == (m2.id, m2.id)
    assert (pay.payment, pay.rule_note) == (D('0.00'), 'Без компенсации: Клиент — Заболел')


def test_record_without_pay_never_bound_by_day(pgroup, make_marker, make_record):
    """У записи нет своей привязки по дню: без начисления-сироты, за которым она
    могла бы пойти, чужой маркер того же дня её не получает и платится."""
    day = datetime.date(2026, 7, 21)
    rec = _record(make_record, pgroup, day, time=T18, compensated=False)
    m2 = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(11, 0))
    _recalc(group_ids=[pgroup['gid']])
    rec.refresh_from_db()
    assert rec.planned_lesson_id is None
    assert _pay(m2).payment == D('250.00')


def test_not_bound_by_day_with_two_markers(pgroup, make_marker, make_record):
    day = datetime.date(2026, 7, 21)
    rec, pay = _orphaned(pgroup, make_marker, make_record, day)
    m2 = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(11, 0))
    m3 = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(12, 0))
    _recalc(group_ids=[pgroup['gid']])
    rec.refresh_from_db()
    pay.refresh_from_db()
    assert (rec.planned_lesson_id, pay.planned_lesson_id) == (None, None)
    # Без записи — платится, как отмена до фичи.
    assert _pay(m2).payment == _pay(m3).payment == D('250.00')


def test_orphan_with_own_key_marker_outside_scope_not_day_candidate(pgroup, make_marker,
                                                                     make_record):
    """Сирота, у которой есть маркер ровно по её ключу (вне охвата пересчёта), —
    его отмена; к соседнему по дню маркеру её не цепляем."""
    day = datetime.date(2026, 7, 21)
    rec, pay = _orphaned(pgroup, make_marker, make_record, day)
    make_marker(pgroup['gid'], pgroup['tid'], day, T18)  # свой маркер, вне охвата
    other = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(11, 0))
    _recalc(marker_ids=[other.id])
    rec.refresh_from_db()
    pay.refresh_from_db()
    assert (rec.planned_lesson_id, pay.planned_lesson_id) == (None, None)
    assert _pay(other).payment == D('250.00')


def test_s2_neighbour_with_own_pay_does_not_block_day_binding(pgroup, make_marker,
                                                               make_record):
    """S2: точечный пересчёт одного маркера (замена преподавателя). Сосед того же
    дня уже со своим начислением — не конкурент: сирота однозначно принадлежит
    новому маркеру, начисление и запись идут к нему (решение «без компенсации»)."""
    from apps.scheduling import repository
    from apps.teachers.models import Teacher
    day = datetime.date(2026, 7, 21)
    m1 = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(10, 0))
    _recalc(marker_ids=[m1.id])
    rec, pay = _orphaned(pgroup, make_marker, make_record, day)
    m2 = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(12, 0))
    substitute = Teacher.objects.create(name='Тест Компенсация Замена',
                                        created_at=timezone.now())

    repository.change_teacher(pgroup['gid'], m2.id, substitute.id)

    rec.refresh_from_db()
    pay.refresh_from_db()
    assert (rec.planned_lesson_id, pay.planned_lesson_id) == (m2.id, m2.id)
    assert (pay.teacher_id, pay.payment) == (substitute.id, D('0.00'))
    assert _pay(m1).payment == D('250.00')


def test_day_binding_skipped_when_two_markers_without_pay(pgroup, make_marker, make_record):
    """В дне два маркера, оба ещё без начисления; точечный пересчёт одного из них
    не видит второго — однозначности нет, сирота остаётся сиротой."""
    day = datetime.date(2026, 7, 21)
    rec, pay = _orphaned(pgroup, make_marker, make_record, day)
    m2 = make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(11, 0))
    make_marker(pgroup['gid'], pgroup['tid'], day, datetime.time(12, 0))

    _recalc(marker_ids=[m2.id])

    rec.refresh_from_db()
    pay.refresh_from_db()
    assert (rec.planned_lesson_id, pay.planned_lesson_id) == (None, None)
    assert _pay(m2).payment == D('250.00')


def test_s6_two_slots_moved_cancellation_keeps_decision(pgroup, make_marker, make_record):
    """S6: в дне два слота, обе отмены прошлые — 10:00 с компенсацией (своё
    начисление), 18:00 без (начисление 0). Отмену 18:00 перенесли на 11:00 (маркер
    удалён, создан новый). Сосед 10:00 со своим начислением не мешает: сирота 0 и
    её запись идут к 11:00 — прошлое не превращается в +250."""
    gid = pgroup['gid']
    day = _past(5)
    m10 = make_marker(gid, pgroup['tid'], day, datetime.time(10, 0))
    _record(make_record, pgroup, day, marker=m10, time=datetime.time(10, 0),
            compensated=True, reason_text='Болезнь')
    _recalc(marker_ids=[m10.id])  # у 10:00 своё начисление
    rec18, pay18 = _orphaned(pgroup, make_marker, make_record, day)
    m11 = make_marker(gid, pgroup['tid'], day, datetime.time(11, 0))

    _recalc(group_ids=[gid])

    rec18.refresh_from_db()
    pay18.refresh_from_db()
    assert (rec18.planned_lesson_id, pay18.planned_lesson_id) == (m11.id, m11.id)
    assert (pay18.payment, pay18.rule_note) == (D('0.00'), 'Без компенсации: Клиент — Заболел')
    assert _pay(m10).payment == D('250.00')
    assert type(pay18).objects.filter(group_id=gid).count() == 2


def test_s6_soft_update_moves_one_of_two_slots(pgroup, make_marker, make_record):
    """S6 через мягкое обновление: удалены ОБА маркера, пересозданы 10:00 (тот же
    ключ) и 11:00 (вместо 18:00). 10:00 забирает своё начисление по ключу в том же
    проходе — значит, он уже не конкурент, и 18:00 → 11:00 привязывается по дню."""
    from apps.eduson_import.sync import facts_without_history, purge_group_facts
    from apps.payroll.recalc import recalculate
    gid = pgroup['gid']
    day = _past(5)
    m10 = make_marker(gid, pgroup['tid'], day, datetime.time(10, 0))
    rec10 = _record(make_record, pgroup, day, marker=m10, time=datetime.time(10, 0),
                    compensated=True, reason_text='Болезнь')
    m18 = make_marker(gid, pgroup['tid'], day, T18)
    rec18 = _record(make_record, pgroup, day, marker=m18, compensated=False)
    _recalc(group_ids=[gid])
    pay10, pay18 = _pay(m10), _pay(m18)

    with facts_without_history():
        purge_group_facts([gid])
    n10 = make_marker(gid, pgroup['tid'], day, datetime.time(10, 0))
    n11 = make_marker(gid, pgroup['tid'], day, datetime.time(11, 0))
    recalculate(group_ids=[gid])

    for obj in (rec10, rec18, pay10, pay18):
        obj.refresh_from_db()
    assert (rec10.planned_lesson_id, pay10.planned_lesson_id) == (n10.id, n10.id)
    assert (rec18.planned_lesson_id, pay18.planned_lesson_id) == (n11.id, n11.id)
    assert (pay10.payment, pay18.payment) == (D('250.00'), D('0.00'))
    assert type(pay18).objects.filter(group_id=gid).count() == 2


def test_two_orphans_same_key_newest_binds(pgroup, make_marker, make_record):
    day = datetime.date(2026, 7, 21)
    old = _record(make_record, pgroup, day, compensated=False, reason_text='Старая')
    new = _record(make_record, pgroup, day, compensated=True, reason_text='Новая')
    marker = make_marker(pgroup['gid'], pgroup['tid'], day, T18)
    _recalc(marker_ids=[marker.id])
    old.refresh_from_db()
    new.refresh_from_db()
    assert (old.planned_lesson_id, new.planned_lesson_id) == (None, marker.id)
    pay = _pay(marker)
    assert pay.payment == D('250.00')
    assert pay.rule_note.startswith('Клиент — Новая · ')


def test_record_with_fk_wins_over_orphan_of_same_key(pgroup, make_marker, make_record):
    day = datetime.date(2026, 7, 21)
    marker = make_marker(pgroup['gid'], pgroup['tid'], day, T18)
    own = _record(make_record, pgroup, day, marker=marker, compensated=True,
                  reason_text='Своя')
    orphan = _record(make_record, pgroup, day, compensated=False, reason_text='Чужая')
    _recalc(marker_ids=[marker.id])
    own.refresh_from_db()
    orphan.refresh_from_db()
    assert (own.planned_lesson_id, orphan.planned_lesson_id) == (marker.id, None)
    assert _pay(marker).rule_note.startswith('Клиент — Своя · ')


# ---------------------------------------------------------------------------
# Сценарии удаления маркера: пересборка плана, смена расписания, мягкое обновление
# ---------------------------------------------------------------------------

def _future(days):
    return msk_now().date() + datetime.timedelta(days=days)


def _past(days):
    return msk_now().date() - datetime.timedelta(days=days)


@pytest.mark.parametrize('operation', ['reset_plan', 'wipe_one_offs'])
def test_marker_removed_record_stays_past_pay_kept_future_deleted(pgroup, make_marker,
                                                                  make_record, operation):
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid = pgroup['gid']
    past_m = make_marker(gid, pgroup['tid'], _past(3), T18)
    future_m = make_marker(gid, pgroup['tid'], _future(3), T18)
    past_r = _record(make_record, pgroup, _past(3), marker=past_m)
    future_r = _record(make_record, pgroup, _future(3), marker=future_m)
    _recalc(group_ids=[gid])
    past_pay = _pay(past_m)

    if operation == 'reset_plan':
        repository.reset_plan(gid)
    else:
        repository.wipe_one_offs(gid, date_from=_past(10))

    past_r.refresh_from_db()
    future_r.refresh_from_db()
    assert (past_r.planned_lesson_id, future_r.planned_lesson_id) == (None, None)
    left = list(CancellationPay.objects.filter(group_id=gid))
    assert [p.id for p in left] == [past_pay.id]
    assert (left[0].payment, left[0].rule_note) == (D('0.00'),
                                                    'Без компенсации: Клиент — Заболел')


def test_soft_update_rebinds_record_and_pay_to_new_marker(pgroup, make_marker, make_record):
    """Мягкое обновление (sync_eduson): факты группы удалены и залиты заново —
    маркер пересоздан на тот же ключ. Запись и начисление (с ручной правкой)
    переходят к новому маркеру, решение «без компенсации» сохраняется."""
    from apps.eduson_import.sync import facts_without_history, purge_group_facts
    from apps.payroll.recalc import recalculate
    gid = pgroup['gid']
    day = _past(5)
    m1 = make_marker(gid, pgroup['tid'], day, T18)
    rec = _record(make_record, pgroup, day, marker=m1)
    _recalc(group_ids=[gid])
    pay = _pay(m1)
    type(pay).objects.filter(id=pay.id).update(payment=D('120.00'), is_manual=True)

    with facts_without_history():
        purge_group_facts([gid])
    m2 = make_marker(gid, pgroup['tid'], day, T18)
    recalculate(group_ids=[gid])

    rec.refresh_from_db()
    pay.refresh_from_db()
    assert rec.planned_lesson_id == m2.id
    assert pay.planned_lesson_id == m2.id
    assert (pay.payment, pay.is_manual, pay.calc_payment) == (D('120.00'), True, D('0.00'))
    assert pay.rule_note == 'Без компенсации: Клиент — Заболел'


def test_s3_soft_update_with_time_shift_keeps_past_zero(pgroup, make_marker, make_record):
    """S3: прошлая отмена без компенсации; мягкое обновление пересоздаёт маркер на
    другое время того же дня, а в этом дне есть ещё одна запись-сирота. Начисление
    привязывается по дню, запись идёт за ним по его исходному ключу — прошлое
    начисление остаётся 0 с той же причиной, а не становится 250 задним числом."""
    from apps.eduson_import.sync import facts_without_history, purge_group_facts
    from apps.payroll.recalc import recalculate
    gid = pgroup['gid']
    day = _past(5)
    m1 = make_marker(gid, pgroup['tid'], day, T18)
    rec = _record(make_record, pgroup, day, marker=m1, compensated=False)
    other = _record(make_record, pgroup, day, time=datetime.time(19, 0), compensated=True,
                    reason_text='Чужая')
    _recalc(group_ids=[gid])
    pay = _pay(m1)
    assert pay.payment == D('0.00')

    with facts_without_history():
        purge_group_facts([gid])
    m2 = make_marker(gid, pgroup['tid'], day, datetime.time(11, 0))
    recalculate(group_ids=[gid])

    rec.refresh_from_db()
    other.refresh_from_db()
    pay.refresh_from_db()
    assert (pay.planned_lesson_id, rec.planned_lesson_id, other.planned_lesson_id) == (
        m2.id, m2.id, None)
    assert (pay.payment, pay.rule_note) == (D('0.00'), 'Без компенсации: Клиент — Заболел')


def test_s1_wiped_future_record_does_not_zero_foreign_marker(pgroup, make_marker,
                                                             make_record):
    """S1: будущая отмена без компенсации снята сменой расписания (wipe_one_offs) —
    начисление удалено, запись осталась сиротой. Позже импорт ставит в этот день
    маркер на другое время: это другая отмена, она платится 250, запись к ней
    не привязывается."""
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    gid = pgroup['gid']
    day = _future(3)
    m1 = make_marker(gid, pgroup['tid'], day, T18)
    rec = _record(make_record, pgroup, day, marker=m1, compensated=False)
    _recalc(group_ids=[gid])
    repository.wipe_one_offs(gid, date_from=_past(1))
    assert not CancellationPay.objects.filter(group_id=gid).exists()

    imported = make_marker(gid, pgroup['tid'], day, datetime.time(11, 0))
    _recalc(group_ids=[gid])

    rec.refresh_from_db()
    assert rec.planned_lesson_id is None
    pay = _pay(imported)
    assert pay.payment == D('250.00')
    assert 'Без компенсации' not in pay.rule_note


def test_reschedule_marker_after_cancel_keeps_record_and_decision(manager_client, pgroup,
                                                                  make_reason):
    from apps.cancellations.models import LessonCancellation
    from apps.scheduling import repository
    reason = make_reason(side='client', compensation=False)
    marker = _cancel(manager_client, pgroup, side='client', reason_id=reason.id,
                     notified_on='2026-07-21')
    rec = LessonCancellation.objects.get(planned_lesson=marker)

    repository.reschedule_lesson(pgroup['gid'], marker.id, datetime.date(2026, 7, 23),
                                 datetime.time(11, 0), None)

    rec.refresh_from_db()
    pay = _pay(marker)
    # Запись — неизменяемый снимок (В6): дата прежняя, привязка по FK та же.
    assert (rec.planned_lesson_id, rec.lesson_date) == (marker.id, datetime.date(2026, 7, 21))
    assert (pay.lesson_date, pay.scheduled_time) == (datetime.date(2026, 7, 23),
                                                     datetime.time(11, 0))
    assert (pay.payment, pay.rule_note) == (D('0.00'), 'Без компенсации: Клиент — Заболел')


def test_substitute_on_cancelled_marker_pays_substitute_with_decision(manager_client, pgroup,
                                                                      make_reason):
    from apps.cancellations.models import LessonCancellation
    from apps.scheduling import repository
    from apps.teachers.models import Teacher
    reason = make_reason(side='teacher', name='Болезнь', compensation=True)
    marker = _cancel(manager_client, pgroup, side='teacher', reason_id=reason.id)
    substitute = Teacher.objects.create(name='Тест Компенсация Замена',
                                        created_at=timezone.now())

    repository.change_teacher(pgroup['gid'], marker.id, substitute.id)

    rec = LessonCancellation.objects.get(planned_lesson=marker)
    pay = _pay(marker)
    # Запись хранит снимок преподавателя на момент отмены; деньги — заменяющему.
    assert rec.teacher_id == pgroup['tid']
    assert (pay.teacher_id, pay.payment) == (substitute.id, D('250.00'))
    assert pay.rule_note.startswith('Преподаватель — Болезнь · ')


# ---------------------------------------------------------------------------
# Блокировки и число запросов
# ---------------------------------------------------------------------------

def test_recalc_locks_markers_then_records_then_money(pgroup, make_marker, make_record):
    day = datetime.date(2026, 7, 21)
    _record(make_record, pgroup, day)  # сирота — привяжется под локом
    make_marker(pgroup['gid'], pgroup['tid'], day, T18)
    with CaptureQueriesContext(connection) as ctx:
        _recalc(group_ids=[pgroup['gid']])
    sqls = [q['sql'] for q in ctx.captured_queries]

    def main_table(sql):
        # Таблица основного FROM: подзапросы Exists в SELECT тоже содержат FROM,
        # но внутри скобок.
        depth = 0
        for i, ch in enumerate(sql):
            depth += ch == '('
            depth -= ch == ')'
            if depth == 0 and sql.startswith(' FROM "', i):
                return sql[i + 7:sql.index('"', i + 7)]

    def locks(table):
        return [i for i, sql in enumerate(sqls)
                if 'FOR UPDATE' in sql and main_table(sql) == table]

    plan, records, money = (locks('planned_lessons'), locks('lesson_cancellations'),
                            locks('payroll_cancellations'))
    assert plan and records and money
    assert max(plan) < min(records) and max(records) < min(money)
    # Привязка решается после привязки начислений (запись идёт за ними), но её
    # строки уже залочены выше — новых локов UPDATE не берёт.
    rebind = next(i for i, sql in enumerate(sqls)
                  if sql.startswith('UPDATE "lesson_cancellations"'))
    assert max(records) < rebind
    # Хук расписания уроки не лочит и не читает.
    assert not [sql for sql in sqls if 'FROM "lessons"' in sql or 'FROM "payroll"' in sql]


@pytest.mark.parametrize('count', [1, 10])
def test_recalc_query_count_does_not_grow_with_markers(pgroup, make_marker, make_record,
                                                       django_assert_max_num_queries, count):
    gid = pgroup['gid']
    for i in range(count):
        day = datetime.date(2026, 8, 1) + datetime.timedelta(days=i)
        # Половина — сироты (привяжутся по ключу), половина — по FK.
        marker = make_marker(gid, pgroup['tid'], day, T18)
        _record(make_record, pgroup, day, marker=marker if i % 2 else None)
    with django_assert_max_num_queries(10):
        stats = _recalc(group_ids=[gid])
    assert stats['created'] == count
