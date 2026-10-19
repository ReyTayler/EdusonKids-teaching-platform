"""
Авто-прогрессия стадий по ОБЩЕЙ истории посещений ученика (подписочная модель):
  - 4 отдельные is_auto-стадии «Не было урока», «Урок 1..3» (миграция 0003,
    переименована миграцией 0009);
  - engine.sync_lesson_stage двигает сделку по мере посещаемости (lesson_attendance);
  - цикл отработан → «Ждём продление» (+due_at); баланс ≤ 0 → «Ждём оплату»;
  - ручной перевод в decision-стадию «замораживает» авто-прогресс.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.renewals import engine
from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage


def _make_group_with_membership(direction_id: int, teacher_id: int, student_id: int,
                                name: str = '__lp_test_group__') -> int:
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
            "lesson_number_offset) "
            "VALUES (%s, %s, %s, false, true, now(), 0) RETURNING id",
            [name, direction_id, teacher_id])
        group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true)", [group_id, student_id])
    return group_id


def _cleanup_group(group_id: int, student_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                    '(SELECT id FROM renewal_deal WHERE student_id = %s)', [student_id])
        cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [student_id])
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.mark.django_db
def test_default_pipeline_has_four_lesson_stages():
    pipe = RenewalPipeline.objects.get(is_default=True)
    stages = list(RenewalStage.objects.filter(
        pipeline=pipe, kind='progress', is_auto=True).order_by('sort_order'))
    assert [s.key for s in stages] == ['no_lesson_yet', 'lesson_1', 'lesson_2', 'lesson_3']
    assert [s.label for s in stages] == ['Не было урока', 'Урок 1', 'Урок 2', 'Урок 3']
    assert not RenewalStage.objects.filter(pipeline=pipe, key='lesson_progress').exists()


@pytest.mark.django_db
def test_ensure_deal_starts_on_no_lesson_yet(make_student):
    sid = make_student()
    deal = engine.ensure_deal(sid, cycle_no=1)
    assert deal.stage.key == 'no_lesson_yet'


@pytest.mark.django_db
def test_sync_lesson_stage_advances_with_attendance(make_student, make_direction,
                                                    make_teacher, make_payment,
                                                    make_attendance):
    sid, did, tid = make_student(), make_direction(), make_teacher()
    make_payment(sid, did, lessons=8)  # баланс > 0 — иначе уедет в «Ждём оплату»
    gid = _make_group_with_membership(did, tid, sid)
    try:
        deal = engine.ensure_deal(sid, cycle_no=1)
        assert deal.stage.key == 'no_lesson_yet'

        make_attendance(sid, gid, tid, count=1)
        engine.sync_lesson_stage(sid)
        deal.refresh_from_db()
        assert deal.stage.key == 'lesson_1'

        make_attendance(sid, gid, tid, count=2, start='2026-06-10')
        engine.sync_lesson_stage(sid)
        deal.refresh_from_db()
        assert deal.stage.key == 'lesson_3'
    finally:
        _cleanup_group(gid, sid)


@pytest.mark.django_db
def test_sync_lesson_stage_does_not_override_manual_decision(make_student, make_direction,
                                                             make_teacher, make_payment,
                                                             make_attendance):
    """
    Ручные (не is_auto) стадии движок не трогает — «Думает» остаётся.
    После завершения цикла сделка стоит на авто-стадии «Ждём продление», уйти
    с которой руками нельзя (from_is_auto, решение пользователя 2026-07-17),
    поэтому ручную «Думает» ставим напрямую через ORM и проверяем, что
    последующий sync её не перебивает.
    """
    sid, did, tid = make_student(), make_direction(), make_teacher()
    make_payment(sid, did, lessons=8)
    gid = _make_group_with_membership(did, tid, sid)
    try:
        deal = engine.ensure_deal(sid, cycle_no=1)
        make_attendance(sid, gid, tid, count=4)
        engine.sync_lesson_stage(sid)
        thinking = RenewalStage.objects.get(pipeline=deal.pipeline, key='thinking')
        deal.stage = thinking
        deal.save(update_fields=['stage'])

        make_attendance(sid, gid, tid, count=3, start='2026-06-10')
        engine.sync_lesson_stage(sid)

        deal.refresh_from_db()
        assert deal.stage.key == 'thinking'
    finally:
        _cleanup_group(gid, sid)


@pytest.mark.django_db
def test_cycle_complete_moves_to_awaiting_renewal(make_student, make_direction,
                                                  make_teacher, make_payment,
                                                  make_attendance):
    """4 суммарных урока отработаны → «Ждём продление» + зафиксирован due_at."""
    sid, did, tid = make_student(), make_direction(), make_teacher()
    make_payment(sid, did, lessons=8)
    gid = _make_group_with_membership(did, tid, sid)
    try:
        make_attendance(sid, gid, tid, count=4)
        deal = engine.ensure_deal(sid, cycle_no=1)
        engine.sync_lesson_stage(sid)
        deal.refresh_from_db()
        assert deal.stage.key == 'awaiting_renewal'
        assert deal.due_at is not None
        assert deal.stage.key != 'no_lesson_yet'  # фикс зацикливания attended % 4
    finally:
        _cleanup_group(gid, sid)


@pytest.mark.django_db
def test_due_at_uses_moscow_calendar_date_not_utc(make_student, make_direction,
                                                  make_teacher, make_payment,
                                                  make_attendance):
    """due_at — календарная дата по МСК, а не по UTC-сессии PostgreSQL. Событие
    в 01:00 по Москве 1 июля (= 22:00 UTC 30 июня) должно дать due_at=2026-07-01,
    а не 2026-06-30 (был баг: engine брал timezone.now().date() — UTC-дату)."""
    from datetime import datetime
    from unittest.mock import patch
    from zoneinfo import ZoneInfo

    sid, did, tid = make_student(), make_direction(), make_teacher()
    make_payment(sid, did, lessons=8)
    gid = _make_group_with_membership(did, tid, sid)
    try:
        make_attendance(sid, gid, tid, count=4)
        deal = engine.ensure_deal(sid, cycle_no=1)
        fake_msk_now = datetime(2026, 7, 1, 1, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        with patch('apps.renewals.engine.msk_now', return_value=fake_msk_now):
            engine.sync_lesson_stage(sid)
        deal.refresh_from_db()
        assert deal.due_at.isoformat() == '2026-07-01'
    finally:
        _cleanup_group(gid, sid)


@pytest.mark.django_db
def test_balance_zero_mid_cycle_moves_to_awaiting_payment(make_student, make_direction,
                                                          make_teacher, make_attendance):
    """Цикл не отработан (2 из 4), оплат нет (баланс ≤ 0) → «Ждём оплату»."""
    sid, did, tid = make_student(), make_direction(), make_teacher()
    gid = _make_group_with_membership(did, tid, sid)
    try:
        make_attendance(sid, gid, tid, count=2)
        deal = engine.ensure_deal(sid, cycle_no=1)
        engine.sync_lesson_stage(sid)
        deal.refresh_from_db()
        assert deal.stage.key == 'awaiting_payment'
        assert deal.due_at is None
    finally:
        _cleanup_group(gid, sid)


@pytest.mark.django_db
def test_awaiting_renewal_wins_over_awaiting_payment(make_student, make_direction,
                                                     make_teacher, make_attendance):
    """Цикл отработан И баланс ≤ 0 → приоритет у «Ждём продление» (долг — бейджем)."""
    sid, did, tid = make_student(), make_direction(), make_teacher()
    gid = _make_group_with_membership(did, tid, sid)
    try:
        make_attendance(sid, gid, tid, count=4)
        deal = engine.ensure_deal(sid, cycle_no=1)
        engine.sync_lesson_stage(sid)
        deal.refresh_from_db()
        assert deal.stage.key == 'awaiting_renewal'
    finally:
        _cleanup_group(gid, sid)


@pytest.mark.django_db
def test_cross_direction_attendance_counts_into_one_history(make_student, make_direction,
                                                            make_teacher, make_payment,
                                                            make_attendance):
    """
    Подписочная модель: посещения РАЗНЫХ направлений складываются в общую
    историю ученика — 2 + 2 урока в двух группах = цикл отработан.
    """
    sid, tid = make_student(), make_teacher()
    did1, did2 = make_direction('__renew_dir_a__'), make_direction('__renew_dir_b__')
    make_payment(sid, did1, lessons=8)
    gid1 = _make_group_with_membership(did1, tid, sid, name='__lp_group_a__')
    gid2 = _make_group_with_membership(did2, tid, sid, name='__lp_group_b__')
    try:
        make_attendance(sid, gid1, tid, count=2, start='2026-06-01')
        make_attendance(sid, gid2, tid, count=2, start='2026-06-10')
        deal = engine.ensure_deal(sid, cycle_no=1)
        engine.sync_lesson_stage(sid)
        deal.refresh_from_db()
        assert deal.stage.key == 'awaiting_renewal'  # 4 суммарных урока
    finally:
        _cleanup_group(gid1, sid)
        _cleanup_group(gid2, sid)


@pytest.mark.django_db
def test_prepaid_cycle2_deal_stays_on_no_lesson_yet(make_student, make_direction,
                                                     make_teacher, make_payment,
                                                     make_attendance):
    """Сделка цикла 2 при attended=2 (ещё идёт цикл 1) стоит на «Не было урока»
    — это и есть П-7: предоплаченный цикл ещё не начался, а не «отработан 1 урок»."""
    sid, did, tid = make_student(), make_direction(), make_teacher()
    make_payment(sid, did, lessons=8)
    gid = _make_group_with_membership(did, tid, sid)
    try:
        make_attendance(sid, gid, tid, count=2)
        engine.ensure_deal(sid, cycle_no=2)
        engine.sync_lesson_stage(sid)
        deal = RenewalDeal.objects.get(student_id=sid, cycle_no=2)
        assert deal.stage.key == 'no_lesson_yet'
    finally:
        _cleanup_group(gid, sid)


@pytest.mark.django_db
def test_attended_total_makeup_does_not_double_count(make_student, make_direction, make_teacher):
    """
    Регрессия (модель потребления 1c): доп.урок НЕ должен задваивать прогресс
    сделки. Компенсируемый пропуск остаётся present=false (0), а сам доп.урок
    (lesson_type='extra', present=true) даёт ровно 1 → _attended_total = 1.0, а
    не 2.0. (До 1c анти-двойной-учёт держался исключением extra; теперь — тем,
    что исходный урок навсегда present=false, а потребление несёт extra-факт.)
    """
    sid, did, tid = make_student(), make_direction(), make_teacher()
    gid = _make_group_with_membership(did, tid, sid, name='__extra_dc_group__')
    try:
        with connection.cursor() as cur:
            # Исходный пропуск: обычный урок, present=false (0).
            cur.execute(
                "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                "lesson_duration_minutes, lesson_type, submitted_by_token) "
                "VALUES (%s,%s,'2026-06-01',1,60,'regular','__dc_miss__') RETURNING id", [gid, tid])
            miss_lid = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) '
                'VALUES (%s,%s,false)', [miss_lid, sid])
            # Доп.урок: extra, present=true (1) — единственный источник потребления.
            cur.execute(
                "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                "lesson_duration_minutes, lesson_type, submitted_by_token) "
                "VALUES (%s,%s,'2026-06-05',1,60,'extra','__dc_test__') RETURNING id", [gid, tid])
            extra_lid = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) '
                'VALUES (%s,%s,true)', [extra_lid, sid])
        try:
            assert engine._attended_total(sid) == 1.0
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = ANY(%s)', [[miss_lid, extra_lid]])
                cur.execute('DELETE FROM lessons WHERE id = ANY(%s)', [[miss_lid, extra_lid]])
    finally:
        _cleanup_group(gid, sid)
