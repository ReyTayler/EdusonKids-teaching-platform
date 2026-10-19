"""
Integration-тесты finances.repository.fifo_inputs + end-to-end compute_fifo
на реальном графе данных.

Покрытие:
  - lots/consumptions строятся по ключу student_id (общий пул, без разбивки по direction);
  - guard subscriptions_count NULL/0 — партия пропускается;
  - легаси-оплата ПОСЛЕ бэкафилла (миграция 0004: subscriptions_count=1,
    direction_id остаётся NULL) — попадает в общий пул наравне с обычными
    оплатами (2026-07-09: убран лишний фильтр direction_id__isnull=False,
    оставшийся от дизайна ДО редизайна общего пула 2026-07-08 — иначе дашборд
    «Долги» считал устаревшие остатки для легаси-учеников);
  - lesson_date приходит строкой 'YYYY-MM-DD';
  - end-to-end compute_fifo на построенных входах сходится до копейки.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.finances import repository
from apps.finances.fifo import compute_fifo

pytestmark = pytest.mark.django_db


def _add_payment(created, student_id, direction_id, subs, unit_price, total, paid_at):
    # lessons_count = subs*4 (как create_payment/бэкафилл 0006 и как читает fifo_inputs
    # для размера партии). Обязателен — CHECK payments_purchase_signs требует NOT NULL.
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'test') RETURNING id",
            [student_id, direction_id, subs, subs * 4, unit_price, total, paid_at],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


def _add_backfilled_legacy_payment(created, student_id, paid_at, amount):
    """Легаси-оплата ПОСЛЕ бэкафилла: direction_id=NULL, subscriptions_count=1,
    lessons_count=4, unit_price == total_amount. Должна попадать в общий пул
    наравне с оплатами, помеченными направлением."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,NULL,1,4,%s,%s,%s,'test') RETURNING id",
            [student_id, amount, amount, paid_at],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


_LESSON_SEQ = [0]


def _add_lesson_with_attendance(created, group_id, teacher_id, student_id, date, duration=60):
    # Уникальный lesson_number — таблица lessons имеет natural-key
    # (lesson_date, group_id, lesson_number, submitted_by_token).
    _LESSON_SEQ[0] += 1
    number = _LESSON_SEQ[0]
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,%s,%s,%s,'regular','test') RETURNING id",
            [group_id, teacher_id, date, number, duration],
        )
        lid = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,true)',
            [lid, student_id],
        )
    created['lessons'].append(lid)
    return lid


def test_fifo_inputs_builds_lots_and_consumptions(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    # Три партии разной цены: 1 подписка ×4=4 урока по 500; 1×4=4 по 450; легаси ×4=4 по 100.
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000, 2000, '2026-05-01')
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 1800, 1800, '2026-05-15')
    # Легаси-оплата ПОСЛЕ бэкафилла (subs=1, direction=NULL) — пулится наравне с остальными.
    _add_backfilled_legacy_payment(graph_cleanup, student_fixture, '2026-05-20', 400)
    # 3 посещения в мае, 4 в июне.
    for _ in range(3):
        _add_lesson_with_attendance(
            graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-05-10'
        )
    for _ in range(4):
        _add_lesson_with_attendance(
            graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-10'
        )

    inputs = repository.fifo_inputs()
    key = str(student_fixture)

    assert key in inputs['keys']
    lots = inputs['lots_by_key'][key]
    # Отбэкафилленная легаси (subs=1, direction=NULL) включена наравне с
    # обычными → ровно 3 партии.
    assert len(lots) == 3
    assert lots[0]['lessons'] == 4
    assert lots[0]['price_per_lesson'] == Decimal('500')
    assert lots[1]['price_per_lesson'] == Decimal('450')
    assert lots[2]['lessons'] == 4
    assert lots[2]['price_per_lesson'] == Decimal('100')
    assert inputs['purchased_by_key'][key] == 12

    cons = inputs['cons_by_key'][key]
    assert len(cons) == 7
    # lesson_date — строка, units — Decimal, direction_id — направление урока.
    assert isinstance(cons[0]['date'], str)
    assert cons[0]['date'] == '2026-05-10'
    assert cons[0]['direction_id'] == direction_fixture
    assert inputs['consumed_by_key'][key] == Decimal('7')

    # End-to-end: совпадает с golden из fifo.test.js (3 по 500 в мае + 1×500+3×450 в июне),
    # третья (легаси, 100/урок) остаётся полностью нетронутой — уходит в remaining_value.
    r = compute_fifo(lots, cons, '2026-06-01', '2026-07-01')
    assert r['worked_off_total'] == Decimal('3350.00')
    assert r['worked_off_month'] == Decimal('1850.00')
    assert r['remaining_value'] == Decimal('850.00')


def test_fifo_inputs_half_lesson_units(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000, 2000, '2026-06-01')
    # 45-мин урок → units = 0.5
    _add_lesson_with_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-10', duration=45
    )
    inputs = repository.fifo_inputs()
    key = str(student_fixture)
    assert inputs['cons_by_key'][key][0]['units'] == Decimal('0.5')
    assert inputs['consumed_by_key'][key] == Decimal('0.5')


def test_fifo_inputs_pools_across_directions(
    teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    """
    Ключевой сценарий редизайна: оплата на direction_fixture (A), урок отработан в
    ДРУГОМ направлении (B) — обе записи должны попасть в ОДИН ключ (student_id),
    т.к. списание теперь общим пулом, без разбивки по направлению.
    """
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, active) "
            "VALUES ('__fifo_dir_b__', true) RETURNING id"
        )
        direction_b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__fifo_group_b__', %s, %s, false, 60, true, 0) RETURNING id",
            [direction_b, teacher_id_fixture],
        )
        group_b = cur.fetchone()[0]
    try:
        _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000, 2000, '2026-05-01')
        lesson_id = _add_lesson_with_attendance(
            graph_cleanup, group_b, teacher_id_fixture, student_fixture, '2026-05-10'
        )
        inputs = repository.fifo_inputs()
        key = str(student_fixture)
        assert len(inputs['lots_by_key'][key]) == 1
        assert len(inputs['cons_by_key'][key]) == 1
        assert inputs['cons_by_key'][key][0]['direction_id'] == direction_b
    finally:
        # Уроки/посещения из group_b должны уйти раньше group_b/direction_b —
        # иначе lessons_group_id_fkey валит DELETE FROM groups. graph_cleanup
        # тоже попробует удалить этот же lesson_id в своём teardown — это
        # no-op на уже удалённой строке.
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [group_b])
            cur.execute('DELETE FROM directions WHERE id = %s', [direction_b])


def test_fifo_lots_use_lessons_count(student_fixture, direction_fixture, graph_cleanup):
    from decimal import Decimal
    from django.db import connection
    from apps.finances.repository import fifo_inputs
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
            "lessons_count, kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s, %s, 99, 4, 'purchase', 1000, 4000, '2026-01-01', 't') RETURNING id",
            [student_fixture, direction_fixture])
        pid = cur.fetchone()[0]
    graph_cleanup['payments'].append(pid)
    inp = fifo_inputs()
    key = str(student_fixture)
    assert inp['lots_by_key'][key][0]['lessons'] == 4
    assert inp['lots_by_key'][key][0]['price_per_lesson'] == Decimal('1000')
