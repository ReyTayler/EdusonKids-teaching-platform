"""
Тесты единого дома баланса (apps/finances/balance.py + repository).

С 2026-07-08 баланс общий пул на ученика (не per-direction) —
apps/finances/repository.py::balance_for_student. paid_by_direction /
attended_by_direction — информационные разбивки, НЕ баланс.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.finances import balance, repository

pytestmark = pytest.mark.django_db


def _add_payment(created, student_id, direction_id, subs, total, paid_at='2026-06-01'):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'test') RETURNING id",
            [student_id, direction_id, subs, subs * 4, total, total, paid_at],
        )
        pid = cur.fetchone()[0]
    created['payments'].append(pid)
    return pid


def _add_lesson_attendance(created, group_id, teacher_id, student_id, date, duration=60):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,%s,1,%s,'regular','test') RETURNING id",
            [group_id, teacher_id, date, duration],
        )
        lid = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,true)',
            [lid, student_id],
        )
    created['lessons'].append(lid)
    return lid


def _add_missed_lesson(created, group_id, teacher_id, student_id, date, duration=60):
    """Обычный урок (lesson_type='regular') с present=False — пропуск, ещё не
    компенсированный доп.уроком."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,%s,1,%s,'regular','test') RETURNING id",
            [group_id, teacher_id, date, duration],
        )
        lid = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,false)',
            [lid, student_id],
        )
    created['lessons'].append(lid)
    return lid


def _add_extra_lesson_with_attendance(created, group_id, teacher_id, student_id, date, duration=60):
    """Факт доп.урока (lesson_type='extra') со своей LessonAttendance(present=True) —
    как создаёт apps.extra_lessons.services.record()."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,%s,1,%s,'extra','test') RETURNING id",
            [group_id, teacher_id, date, duration],
        )
        lid = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,true)',
            [lid, student_id],
        )
    created['lessons'].append(lid)
    return lid


def test_total_balance_is_int_when_whole(student_fixture, direction_fixture, graph_cleanup):
    # 1 подписка ×4 = 4 куплено, 0 посещений → total_balance 4 (int).
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000)
    result = balance.get_student_balance(student_fixture)
    assert result['total_balance'] == 4
    assert isinstance(result['total_balance'], int)


def test_total_balance_is_float_with_half_lesson(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000)
    # 45-мин урок → attended 0.5 → total_balance 3.5 (float)
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-10', duration=45
    )
    result = balance.get_student_balance(student_fixture)
    assert result['total_balance'] == 3.5
    assert isinstance(result['total_balance'], float)


def test_balance_for_student_matches(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000)
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-10', duration=60
    )
    bal = repository.balance_for_student(student_fixture)
    assert bal == 3
    assert isinstance(bal, int)


def test_balance_pools_across_directions(
    teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    """
    Ключевой сценарий редизайна: оплата за направление A, но урок отработан в
    ДРУГОМ направлении B — списывается из общего пула, а не остаётся зависшей.
    """
    # lid/group_b/direction_b инициализируем заранее: если setup упадёт, finally
    # не должен маскировать ошибку NameError'ом и не должен утечь строки в dev-БД.
    direction_b = group_b = lid = None
    try:
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO directions (name, active) "
                "VALUES ('__fin_dir_b__', true) RETURNING id"
            )
            direction_b = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
                "lesson_duration_minutes, active, lesson_number_offset) "
                "VALUES ('__fin_group_b__', %s, %s, false, 60, true, 0) RETURNING id",
                [direction_b, teacher_id_fixture],
            )
            group_b = cur.fetchone()[0]
        # Оплата на направление A (direction_fixture) — 4 урока.
        _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000)
        # Урок отработан на направлении B (group_b).
        lid = _add_lesson_attendance(
            graph_cleanup, group_b, teacher_id_fixture, student_fixture, '2026-06-10', duration=60
        )
        result = balance.get_student_balance(student_fixture)
        # 4 куплено на A, 1 отработан на B → общий баланс 3 (списался из общего пула).
        assert result['total_balance'] == 3
        paid_a = next(d for d in result['paid_by_direction'] if d['direction_id'] == direction_fixture)
        assert paid_a['total_paid_amount'] == 2000
        attended_b = next(d for d in result['attended_by_direction'] if d['direction_id'] == direction_b)
        assert attended_b['attended_lessons'] == 1
    finally:
        # ВАЖНО: удалить lesson_attendance/lessons для group_b ДО удаления group/direction
        # (graph_cleanup teardown идёт позже; FK lessons→groups иначе падает).
        with connection.cursor() as cur:
            if lid is not None:
                cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
                cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
                if lid in graph_cleanup['lessons']:
                    graph_cleanup['lessons'].remove(lid)
            if group_b is not None:
                cur.execute('DELETE FROM groups WHERE id = %s', [group_b])
            if direction_b is not None:
                cur.execute('DELETE FROM directions WHERE id = %s', [direction_b])


def test_balance_empty_student(student_fixture, graph_cleanup):
    result = balance.get_student_balance(student_fixture)
    assert result['paid_by_direction'] == []
    assert result['attended_by_direction'] == []
    assert result['total_balance'] == 0
    assert result['payments'] == []


def test_balances_for_students_batches_multiple(
    teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    """Один вызов на несколько student_id — каждый получает свой баланс, без N+1."""
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000)  # 4 урока куплено
    other_student_id = None
    try:
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO students (full_name) "
                "VALUES ('__fin_student_2__') RETURNING id"
            )
            other_student_id = cur.fetchone()[0]
        result = repository.balances_for_students([student_fixture, other_student_id])
        assert result[student_fixture] == 4
        assert result[other_student_id] == 0
    finally:
        if other_student_id is not None:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM students WHERE id = %s', [other_student_id])


def test_balances_for_students_matches_single(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    """Батч-результат совпадает с balance_for_student для того же ученика."""
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000)
    _add_lesson_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-10', duration=60
    )
    batch = repository.balances_for_students([student_fixture])
    single = repository.balance_for_student(student_fixture)
    assert batch[student_fixture] == single == 3


def test_balances_for_students_empty_input():
    """Пустой список id → пустой словарь, без похода в БД с IN ()."""
    assert repository.balances_for_students([]) == {}


def test_balance_uses_lessons_count(student_fixture, direction_fixture, graph_cleanup):
    from django.db import connection
    from apps.finances.repository import balance_for_student
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
            "lessons_count, kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s, %s, 99, 4, 'purchase', 1000, 4000, '2026-01-01', 't') RETURNING id",
            [student_fixture, direction_fixture])
        pid = cur.fetchone()[0]
    graph_cleanup['payments'].append(pid)
    # Источник правды — lessons_count (4), НЕ subscriptions_count*4 (=396).
    assert balance_for_student(student_fixture) == 4


def test_get_student_balance_exposes_remaining_value(student_fixture, direction_fixture, graph_cleanup):
    from django.db import connection
    from apps.finances.balance import get_student_balance
    from apps.finances.repository import student_fifo_remaining, _js_number
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
            "lessons_count, kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,1,4,'purchase',1000,4000,'2026-01-01','t') RETURNING id",
            [student_fixture, direction_fixture])
        pid = cur.fetchone()[0]
    graph_cleanup['payments'].append(pid)
    result = get_student_balance(student_fixture)
    assert 'remaining_value' in result
    assert result['remaining_value'] == 4000
    assert result['remaining_value'] == _js_number(student_fifo_remaining(student_fixture)['remaining_value'])


def test_extra_lesson_does_not_double_count_balance(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    """
    Регрессия (доп.уроки, модель потребления 1c): потребление идёт от ФАКТА
    доп.урока (Lesson lesson_type='extra', present=True), а ИСХОДНЫЙ пропуск
    остаётся present=False навсегда. Списывается РОВНО ОДИН урок (extra), а не
    два: исходный не даёт потребления (present=False), extra даёт ровно 1.
    (До 1c было наоборот — исходный флипался в present=True, а extra исключался;
    числа те же, но теперь источник — сам факт доп.урока.)
    """
    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000)  # 4 куплено
    assert repository.balance_for_student(student_fixture) == 4

    _add_missed_lesson(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-05', duration=60
    )
    # Пропуск ещё не компенсирован (present=False) — баланс не тронут.
    assert repository.balance_for_student(student_fixture) == 4

    # Факт доп.урока (present=True) — ЕДИНСТВЕННЫЙ источник потребления. Исходный
    # урок НЕ флипаем (модель 1c: он остаётся present=False).
    _add_extra_lesson_with_attendance(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-10', duration=60
    )

    # Ровно ОДИН урок списан (4 - 1 = 3): extra даёт 1, исходный present=False даёт 0.
    assert repository.balance_for_student(student_fixture) == 3
    assert repository.balances_for_students([student_fixture])[student_fixture] == 3

    # attended_by_direction_rows / fifo_inputs — тот же инвариант в остальных
    # 2 местах, где считается "отработано".
    attended_rows = repository.attended_by_direction_rows(student_fixture)
    assert attended_rows[0]['attended_lessons'] == 1

    fifo_data = repository.fifo_inputs()
    key = str(student_fixture)
    assert fifo_data['consumed_by_key'][key] == 1


def test_worked_off_month_uses_extra_lesson_completion_date(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, graph_cleanup
):
    """
    Регрессия (решение пользователя 2026-07-16): если доп.урок проведён в МЕСЯЦЕ,
    отличном от месяца пропущенного урока, «отработанные» деньги должны относиться
    к месяцу ФАКТИЧЕСКОГО проведения доп.урока (когда преподаватель реально
    отработал и получил за это зарплату), а не к месяцу исходного пропуска.
    """
    from apps.extra_lessons import services as extra_services
    from apps.extra_lessons.models import AbsenceResolution
    from apps.extra_lessons.repository import get_resolution_full
    from apps.finances.fifo import compute_fifo

    class _FakeRequest:
        META = {}
        user = None

    _add_payment(graph_cleanup, student_fixture, direction_fixture, 1, 2000, '2026-06-01')  # 4 куплено, 500р/урок

    missed_lid = _add_missed_lesson(
        graph_cleanup, group_fixture, teacher_id_fixture, student_fixture, '2026-06-05', duration=60
    )

    created = extra_services.create_assignment(
        {
            'missed_lesson_id': missed_lid, 'teacher_id': teacher_id_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-07-10',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    try:
        extra_services.record(
            rid, teacher_id=teacher_id_fixture, present=True,
            record_url=None, submitted_by_token='test', submit_date='2026-07-10',
            request=_FakeRequest(),
        )

        inp = repository.fifo_inputs()
        key = str(student_fixture)
        fifo = compute_fifo(
            inp['lots_by_key'].get(key, []), inp['cons_by_key'].get(key, []),
            '0001-01-01', '9999-12-31',
        )
        # Отработано относится к июлю (доп.урок), НЕ к июню (месяц пропуска).
        assert fifo['worked_off_by_month'].get('2026-07') == 500
        assert '2026-06' not in fifo['worked_off_by_month']
    finally:
        full = get_resolution_full(rid)
        if full and full['status'] == 'makeup_done':
            extra_services.delete_fact(rid, _FakeRequest())
        AbsenceResolution.objects.filter(id=rid).delete()
