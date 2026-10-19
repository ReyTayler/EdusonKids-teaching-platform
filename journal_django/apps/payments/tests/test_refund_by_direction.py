"""
Возврат средств в разрезе направлений.

Возврат пишется НЕ одной пул-строкой, а по строке на каждое направление, чьи
партии реально гасятся. Иначе лимит курса (cap в create_payment) остаётся
занятым и вернувшийся ученик не может оплатить тот же курс заново.

Фикстуры уроков в conftest требуют преподавателя в БД (иначе skip), поэтому
потребление здесь делается локальным графом teacher→group→lesson→attendance.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.payments import repository

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Локальные помощники
# ---------------------------------------------------------------------------

@pytest.fixture
def teacher():
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__refund_dir_teacher__') RETURNING id")
        tid = cur.fetchone()[0]
    yield tid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [tid])


def _direction(name: str, total_lessons: int) -> int:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO directions (name, total_lessons, active) VALUES (%s, %s, true) RETURNING id',
            [name, total_lessons],
        )
        return cur.fetchone()[0]


def _attend(student_id: int, teacher_id: int, direction_id: int, count: int,
            date: str = '2026-02-10', duration: int = 60) -> None:
    """Провести `count` уроков ученику в направлении (гасит партии FIFO)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__refund_dir_group__', %s, %s, false, %s, true, 0) RETURNING id",
            [direction_id, teacher_id, duration],
        )
        group_id = cur.fetchone()[0]
        for n in range(1, count + 1):
            cur.execute(
                "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                "lesson_duration_minutes, lesson_type, submitted_by_token) "
                "VALUES (%s, %s, %s, %s, %s, 'group', 'test') RETURNING id",
                [group_id, teacher_id, date, n, duration],
            )
            lesson_id = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
                [lesson_id, student_id],
            )


@pytest.fixture
def cleanup(student_fixture):
    """Сносит весь локальный граф ученика после теста (payments → attendance → lessons → groups)."""
    directions: list[int] = []
    yield directions
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE student_id = %s', [student_fixture])
        cur.execute(
            'DELETE FROM lesson_attendance WHERE lesson_id IN '
            "(SELECT id FROM lessons WHERE group_id IN "
            "(SELECT id FROM groups WHERE name = '__refund_dir_group__'))",
        )
        cur.execute("DELETE FROM lessons WHERE group_id IN "
                    "(SELECT id FROM groups WHERE name = '__refund_dir_group__')")
        cur.execute("DELETE FROM groups WHERE name = '__refund_dir_group__'")
        for did in directions:
            cur.execute('DELETE FROM directions WHERE id = %s', [did])


def _buy(student_id: int, direction_id: int, lessons: int, total: str, paid_at: str = '2026-01-01'):
    res = repository.create_payment({
        'student_id': student_id, 'direction_id': direction_id,
        'lessons_count': lessons, 'total_amount': total, 'paid_at': paid_at,
    })
    assert 'payment' in res, res
    return res['payment']


# ---------------------------------------------------------------------------
# Строки возврата
# ---------------------------------------------------------------------------

def test_refund_writes_row_per_direction(student_fixture, cleanup):
    dir_a = _direction('__refund_dir_a__', 8)
    dir_b = _direction('__refund_dir_b__', 8)
    cleanup += [dir_a, dir_b]
    _buy(student_fixture, dir_a, 4, '4000.00')
    _buy(student_fixture, dir_b, 4, '2000.00')

    res = repository.refund_student(student_fixture, created_by='Админ')

    rows = {r['direction_id']: r for r in res['refunds']}
    assert set(rows) == {dir_a, dir_b}
    assert rows[dir_a]['lessons_count'] == -4
    assert rows[dir_a]['total_amount'] == Decimal('-4000.00')
    assert rows[dir_b]['total_amount'] == Decimal('-2000.00')
    assert all(r['kind'] == 'refund' for r in res['refunds'])
    assert res['refunded_amount'] == Decimal('6000.00')
    assert res['new_balance'] == 0


def test_refund_rows_sum_to_refunded_amount(student_fixture, teacher, cleanup):
    """Частично отработанный курс: возвращается только непогашенный хвост."""
    from apps.finances.repository import balance_for_student, student_fifo_remaining
    dir_a = _direction('__refund_dir_a__', 8)
    cleanup.append(dir_a)
    _buy(student_fixture, dir_a, 8, '8000.00')
    _attend(student_fixture, teacher, dir_a, 3)

    res = repository.refund_student(student_fixture, created_by='Админ')

    assert res['refunded_amount'] == Decimal('5000.00')
    assert sum(r['total_amount'] for r in res['refunds']) == Decimal('-5000.00')
    assert sum(Decimal(str(r['lessons_count'])) for r in res['refunds']) == Decimal('-5')
    assert balance_for_student(student_fixture) == 0
    assert student_fifo_remaining(student_fixture)['remaining_value'] == Decimal('0.00')


def test_refund_of_extra_topup_stays_without_direction(student_fixture, cleanup):
    """Доплата сверх курса возвращается строкой без направления — лимит курса
    она не занимала, значит и освобождать ей нечего."""
    dir_a = _direction('__refund_dir_a__', 4)
    cleanup.append(dir_a)
    _buy(student_fixture, dir_a, 4, '4000.00')
    extra = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 1, 'total_amount': '800.00', 'paid_at': '2026-01-05', 'kind': 'extra',
    })
    assert 'payment' in extra, extra

    res = repository.refund_student(student_fixture, created_by='Админ')

    rows = {r['direction_id']: r for r in res['refunds']}
    assert rows[dir_a]['total_amount'] == Decimal('-4000.00')
    assert rows[None]['total_amount'] == Decimal('-800.00')
    assert res['refunded_amount'] == Decimal('4800.00')


def test_refund_nothing_to_refund_unchanged(student_fixture):
    assert repository.refund_student(student_fixture) == {'error': 'nothing_to_refund'}


# ---------------------------------------------------------------------------
# Лимит курса после возврата
# ---------------------------------------------------------------------------

def test_repurchase_same_direction_allowed_after_refund(student_fixture, cleanup):
    """Ключевой сценарий: курс выкуплен целиком → возврат → ученик вернулся."""
    dir_a = _direction('__refund_dir_a__', 8)
    cleanup.append(dir_a)
    _buy(student_fixture, dir_a, 8, '8000.00')
    repository.refund_student(student_fixture, created_by='Админ')

    again = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 8, 'total_amount': '8000.00', 'paid_at': '2026-09-01',
    })

    assert 'payment' in again, again


def test_repurchase_limited_to_unrefunded_part(student_fixture, teacher, cleanup):
    """Отходил 3 из 8 → вернули 5 → в курсе снова свободно ровно 5 уроков."""
    dir_a = _direction('__refund_dir_a__', 8)
    cleanup.append(dir_a)
    _buy(student_fixture, dir_a, 8, '8000.00')
    _attend(student_fixture, teacher, dir_a, 3)
    repository.refund_student(student_fixture, created_by='Админ')

    too_much = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 8, 'total_amount': '8000.00', 'paid_at': '2026-09-01',
    })
    assert too_much['error'] == 'cap_exceeded'
    assert too_much['already'] == 3

    fits = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 4, 'total_amount': '4000.00', 'paid_at': '2026-09-01',
    })
    assert 'payment' in fits, fits


def test_refund_of_extra_does_not_free_course_limit(student_fixture, cleanup):
    """Возврат доплаты сверх курса лимит курса не расширяет."""
    dir_a = _direction('__refund_dir_a__', 4)
    cleanup.append(dir_a)
    _buy(student_fixture, dir_a, 4, '4000.00')
    extra = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 1, 'total_amount': '800.00', 'paid_at': '2026-01-05', 'kind': 'extra',
    })
    assert 'payment' in extra, extra
    repository.refund_student(student_fixture, created_by='Админ')

    # Вернулось 4 урока курса + 1 доплата. Курс на 4 → снова доступно ровно 4, не 5.
    over = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 8, 'total_amount': '8000.00', 'paid_at': '2026-09-01',
    })
    assert over['error'] == 'cap_exceeded'
    fits = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 4, 'total_amount': '4000.00', 'paid_at': '2026-09-01',
    })
    assert 'payment' in fits, fits


def test_legacy_pool_refund_does_not_free_limit(student_fixture, cleanup):
    """Старые пул-возвраты (direction_id IS NULL) лимит не трогают — регрессия-страж."""
    dir_a = _direction('__refund_dir_a__', 8)
    cleanup.append(dir_a)
    _buy(student_fixture, dir_a, 8, '8000.00')
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s, NULL, NULL, -8, 'refund', 0, -8000, '2026-06-01', 'legacy')",
            [student_fixture],
        )

    again = repository.create_payment({
        'student_id': student_fixture, 'direction_id': dir_a,
        'lessons_count': 4, 'total_amount': '4000.00', 'paid_at': '2026-09-01',
    })
    assert again['error'] == 'cap_exceeded'
