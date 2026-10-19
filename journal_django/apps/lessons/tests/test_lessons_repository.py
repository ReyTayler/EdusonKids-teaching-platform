"""
Integration-тесты слоя lessons (реальная БД, managed=False).

create/attendance-toggle идут через apps.lessons.services (record_lesson —
единое ядро, см. apps.lessons.repository для low-level ORM-хелперов). Остальное
(update_lesson/delete_lesson_full/list_lessons) — repository напрямую, как раньше.

Покрытие:
  - create (через services.create_lesson_full): INSERT урока + attendance +
    payroll (сервер считает сам), инкремент lessons_done, link_facts.
  - half-lesson (45 мин → шаг 0.5) vs обычный (60 мин → шаг 1).
  - get_lesson_full: meta + attendance[] + payroll, None для отсутствующего.
  - update_lesson: COALESCE-семантика, original_teacher_id nullable.
  - delete_lesson_full: откат lessons_done, CASCADE attendance, удаление payroll,
    возврат planned_lessons в pending.
  - update_attendance_cell: дельта lessons_done (false→true→false), UPSERT,
    пересчёт payroll, блокировка без оплаченных уроков.
  - list_lessons: фильтры, сорт, контракт {rows,total,page,page_size}.
  - DATE-инвариант: lesson_date ввод == вывод без сдвига.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import connection

from apps.lessons import repository, services
from apps.lessons.exceptions import AttendanceLockedByTransfer, UnpaidAttendanceBlocked

pytestmark = pytest.mark.django_db


def _delete_lesson(lesson_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


# ---------------------------------------------------------------------------
# create (services.create_lesson_full → record_lesson)
# ---------------------------------------------------------------------------

def test_create_lesson_increments_lessons_done_step_1(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-01',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    })
    lesson_id = result['lesson_id']
    try:
        # 60-мин урок → шаг 1
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')
        full = repository.get_lesson_full(lesson_id)
        # repo-слой отдаёт psycopg2 date; строку '2026-03-01' даёт renderer (см. API-тест)
        assert full['lesson_date'] == datetime.date(2026, 3, 1)
        assert len(full['attendance']) == 1
        assert full['attendance'][0]['present'] is True
    finally:
        _delete_lesson(lesson_id)


def test_create_lesson_half_lesson_step_05(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-02',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 45,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    })
    try:
        # 45-мин урок → шаг 0.5
        assert lessons_done(group_fixture, student_fixture) == Decimal('0.5')
    finally:
        _delete_lesson(result['lesson_id'])


def test_create_lesson_absent_student_no_increment(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-03',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': False}],
    })
    try:
        assert lessons_done(group_fixture, student_fixture) == Decimal('0.0')
    finally:
        _delete_lesson(result['lesson_id'])


def test_create_lesson_with_payroll(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture
):
    """Payroll теперь всегда считается сервером из attendance — total=1,
    present=1 → small-group-full formula = 500. Клиентский payroll больше не
    принимается (payroll не передаём вообще)."""
    result = services.create_lesson_full({
        'lesson_date': '2026-03-04',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    })
    lesson_id = result['lesson_id']
    try:
        full = repository.get_lesson_full(lesson_id)
        assert full['payroll'] is not None
        assert full['payroll']['total_students'] == 1
        assert full['payroll']['present_count'] == 1
        assert full['payroll']['payment'] == Decimal('500.00')
    finally:
        _delete_lesson(lesson_id)


def test_create_lesson_blocked_without_paid_balance(
    group_fixture, teacher_id_fixture, student_fixture,
):
    """Ученик без оплаченных уроков (membership без payments) + present:true →
    UnpaidAttendanceBlocked, урок не создаётся вообще."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, 0, true) RETURNING id',
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
    try:
        with pytest.raises(UnpaidAttendanceBlocked):
            services.create_lesson_full({
                'lesson_date': '2026-03-04',
                'group_id': group_fixture,
                'teacher_id': teacher_id_fixture,
                'lesson_number': 1,
                'lesson_duration_minutes': 60,
                'attendance': [{'student_id': student_fixture, 'present': True}],
            })
        with connection.cursor() as cur:
            cur.execute(
                'SELECT COUNT(*) FROM lessons WHERE group_id = %s AND lesson_date = %s',
                [group_fixture, '2026-03-04'],
            )
            assert cur.fetchone()[0] == 0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


# ---------------------------------------------------------------------------
# get_lesson_full
# ---------------------------------------------------------------------------

def test_get_lesson_full_missing_returns_none():
    assert repository.get_lesson_full(999_999_999) is None


# ---------------------------------------------------------------------------
# update_lesson
# ---------------------------------------------------------------------------

def test_update_lesson_coalesce(group_fixture, teacher_id_fixture):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-05',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
    })
    lesson_id = result['lesson_id']
    try:
        # Передаём только lesson_type — остальное должно сохраниться.
        updated = repository.update_lesson(lesson_id, {'lesson_type': 'substitution'})
        assert updated['lesson_type'] == 'substitution'
        assert updated['lesson_date'] == datetime.date(2026, 3, 5)
    finally:
        _delete_lesson(lesson_id)


def test_update_lesson_original_teacher_explicit_null(group_fixture, teacher_id_fixture):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-06',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'original_teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
    })
    lesson_id = result['lesson_id']
    try:
        # Явный null → должен обнулиться.
        updated = repository.update_lesson(lesson_id, {'original_teacher_id': None})
        assert updated['original_teacher_id'] is None

        # Повторно ставим, затем НЕ передаём ключ → должен сохраниться.
        repository.update_lesson(lesson_id, {'original_teacher_id': teacher_id_fixture})
        again = repository.update_lesson(lesson_id, {'lesson_number': 2})
        assert again['original_teacher_id'] == teacher_id_fixture
    finally:
        _delete_lesson(lesson_id)


def test_update_lesson_missing_returns_none():
    assert repository.update_lesson(999_999_999, {'lesson_type': 'regular'}) is None


# ---------------------------------------------------------------------------
# delete_lesson_full
# ---------------------------------------------------------------------------

def test_delete_lesson_rolls_back_lessons_done(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-07',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    })
    lesson_id = result['lesson_id']
    assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')

    ok = repository.delete_lesson_full(lesson_id)
    assert ok is True
    # lessons_done откатился
    assert lessons_done(group_fixture, student_fixture) == Decimal('0.0')
    # attendance удалён по CASCADE
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        assert cur.fetchone()[0] == 0


def test_delete_lesson_unlinks_planned_lesson(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    """Удаление урока возвращает связанную плановую строку в 'pending' (не
    остаётся зависшей 'done' без факта)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, "
            "scheduled_time, teacher_id, status, created_at, updated_at) "
            "VALUES (%s, 1, 1, '2026-03-07', '10:00', %s, 'pending', NOW(), NOW()) "
            "RETURNING id",
            [group_fixture, teacher_id_fixture],
        )
        planned_id = cur.fetchone()[0]

    result = services.create_lesson_full({
        'lesson_date': '2026-03-07',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    })
    lesson_id = result['lesson_id']
    try:
        with connection.cursor() as cur:
            cur.execute(
                'SELECT fact_lesson_id, status FROM planned_lessons WHERE id = %s',
                [planned_id],
            )
            fact_lesson_id, status = cur.fetchone()
        assert fact_lesson_id == lesson_id
        assert status == 'done'

        assert repository.delete_lesson_full(lesson_id) is True

        with connection.cursor() as cur:
            cur.execute(
                'SELECT fact_lesson_id, status FROM planned_lessons WHERE id = %s',
                [planned_id],
            )
            fact_lesson_id, status = cur.fetchone()
        assert fact_lesson_id is None
        assert status == 'pending'
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])


def test_delete_lesson_missing_returns_false():
    assert repository.delete_lesson_full(999_999_999) is False


# ---------------------------------------------------------------------------
# update_attendance_cell
# ---------------------------------------------------------------------------

def test_attendance_toggle_delta(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-08',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
    })
    lesson_id = result['lesson_id']
    try:
        # Нет посещения → ставим present=true → +1
        ok = repository.update_attendance_cell(lesson_id, student_fixture, True)
        assert ok is True
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')

        # true → false → -1
        repository.update_attendance_cell(lesson_id, student_fixture, False)
        assert lessons_done(group_fixture, student_fixture) == Decimal('0.0')

        # false → true снова → +1
        repository.update_attendance_cell(lesson_id, student_fixture, True)
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')
    finally:
        _delete_lesson(lesson_id)


def _present(lesson_id: int, student_id: int):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT present FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
            [lesson_id, student_id],
        )
        return cur.fetchone()[0]


def test_attendance_toggle_flips_present_and_lessons_done(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    """update_attendance_cell — простой toggle посещаемости: false→true списывает
    урок (lessons_done += вес), true→false возвращает. Никаких «сгораний» задним
    числом: ретроактивная отметка пропуска теперь идёт через раздел «Доп.уроки»
    (burned-Lesson), а не флипом ячейки исходного урока."""
    result = services.create_lesson_full({
        'lesson_date': '2026-03-08',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': False}],
    })
    lesson_id = result['lesson_id']
    try:
        assert _present(lesson_id, student_fixture) is False
        assert lessons_done(group_fixture, student_fixture) == Decimal('0')

        repository.update_attendance_cell(lesson_id, student_fixture, True)
        assert _present(lesson_id, student_fixture) is True
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')

        # true → true (no-op) не двигает счётчик.
        repository.update_attendance_cell(lesson_id, student_fixture, True)
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')

        # Откат true → false возвращает.
        repository.update_attendance_cell(lesson_id, student_fixture, False)
        assert _present(lesson_id, student_fixture) is False
        assert lessons_done(group_fixture, student_fixture) == Decimal('0')
    finally:
        _delete_lesson(lesson_id)


def test_attendance_toggle_missing_lesson_returns_false(student_fixture):
    assert repository.update_attendance_cell(999_999_999, student_fixture, True) is False


def test_attendance_toggle_recomputes_payroll(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lessons_done
):
    """
    Переключение ячейки посещаемости пересчитывает present_count И payment по
    фактическому present_total (спец-надбавок «сгорания» больше нет — payment
    считается напрямую calculate_payment). penalty не трогается (она про
    своевременность исходной записи урока).
    """
    result = services.create_lesson_full({
        'lesson_date': '2026-03-08',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': False}],
    })
    lesson_id = result['lesson_id']
    try:
        before = repository.get_lesson_full(lesson_id)
        assert before['payroll']['present_count'] == 0
        assert before['payroll']['payment'] == Decimal('0.00')

        ok = repository.update_attendance_cell(lesson_id, student_fixture, True)
        assert ok is True

        after = repository.get_lesson_full(lesson_id)
        assert after['payroll']['present_count'] == 1
        # total_students=1, present=1 → малая группа, все пришли → 500.
        assert after['payroll']['payment'] == Decimal('500.00')

        # Откат (true → false) возвращает payment к базовому 0.
        repository.update_attendance_cell(lesson_id, student_fixture, False)
        reverted = repository.get_lesson_full(lesson_id)
        assert reverted['payroll']['present_count'] == 0
        assert reverted['payroll']['payment'] == Decimal('0.00')
    finally:
        _delete_lesson(lesson_id)


def test_attendance_toggle_blocked_when_no_paid_balance(
    group_fixture, teacher_id_fixture, student_fixture,
):
    """Ученик без оплаченных уроков (membership без payments) — переключить в
    present:true нельзя, поднимает UnpaidAttendanceBlocked, ничего не меняется.
    НАПОМИНАНИЕ: как и предыдущий тест, это поведение реализует СЛЕДУЮЩАЯ
    задача плана, не ваша — если падает, это ожидаемо сейчас."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, 0, true) RETURNING id',
            [group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]

    result = services.create_lesson_full({
        'lesson_date': '2026-03-08',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': False}],
    })
    lesson_id = result['lesson_id']
    try:
        with pytest.raises(UnpaidAttendanceBlocked):
            repository.update_attendance_cell(lesson_id, student_fixture, True)
        with connection.cursor() as cur:
            cur.execute(
                'SELECT present FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                [lesson_id, student_fixture],
            )
            assert cur.fetchone()[0] is False
    finally:
        _delete_lesson(lesson_id)
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def test_attendance_cell_blocked_for_locked_transferred_student(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    """Ученик переведён с B=5 отработанными; урок с lesson_number=3 (<=5) блокирован."""
    # Вторая группа того же направления — источник, откуда «переведён» ученик.
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) VALUES ('__les_locked_src__', %s, %s, false, 60, true, 0) "
            "RETURNING id",
            [direction_fixture, teacher_id_fixture],
        )
        src_group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 5, false) RETURNING id",
            [src_group_id, student_fixture],
        )
        src_membership_id = cur.fetchone()[0]
        cur.execute(
            'UPDATE group_memberships SET transferred_from_id = %s '
            'WHERE group_id = %s AND student_id = %s',
            [src_membership_id, group_fixture, student_fixture],
        )

    result = services.create_lesson_full({
        'lesson_date': '2026-03-08', 'group_id': group_fixture, 'teacher_id': teacher_id_fixture,
        'lesson_number': 3, 'lesson_duration_minutes': 60,
    })
    lesson_id = result['lesson_id']
    try:
        with pytest.raises(AttendanceLockedByTransfer):
            repository.update_attendance_cell(lesson_id, student_fixture, True)
        with pytest.raises(AttendanceLockedByTransfer):
            repository.update_attendance_cell(lesson_id, student_fixture, False)
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
            cur.execute('UPDATE group_memberships SET transferred_from_id = NULL '
                        'WHERE group_id = %s AND student_id = %s', [group_fixture, student_fixture])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [src_membership_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [src_group_id])


def test_attendance_cell_allowed_once_group_catches_up(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    """B=5; урок с lesson_number=6 (>5) — разрешён."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) VALUES ('__les_locked_src2__', %s, %s, false, 60, true, 0) "
            "RETURNING id",
            [direction_fixture, teacher_id_fixture],
        )
        src_group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 5, false) RETURNING id",
            [src_group_id, student_fixture],
        )
        src_membership_id = cur.fetchone()[0]
        cur.execute(
            'UPDATE group_memberships SET transferred_from_id = %s '
            'WHERE group_id = %s AND student_id = %s',
            [src_membership_id, group_fixture, student_fixture],
        )

    result = services.create_lesson_full({
        'lesson_date': '2026-03-08', 'group_id': group_fixture, 'teacher_id': teacher_id_fixture,
        'lesson_number': 6, 'lesson_duration_minutes': 60,
    })
    lesson_id = result['lesson_id']
    try:
        assert repository.update_attendance_cell(lesson_id, student_fixture, True) is True
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
            cur.execute('UPDATE group_memberships SET transferred_from_id = NULL '
                        'WHERE group_id = %s AND student_id = %s', [group_fixture, student_fixture])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [src_membership_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [src_group_id])


def test_attendance_cell_set_free_postfactum_frees_student_not_payroll(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture,
    membership_fixture, lessons_done,
):
    """Проставление «бесплатно» ПОСТФАКТУМ на проведённом уроке (типовой результат
    разрешения спора): один из двух present-учеников → is_free. Баланс УЧЕНИКА
    восстанавливается (списание снимается), зарплата преподавателя не меняется
    (free оплачивается как обычное присутствие), прогресс не меняется (present остаётся)."""
    from apps.finances.repository import balance_for_student

    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_free_pf__') RETURNING id")
        s2 = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)", [group_fixture, s2])
        cur.execute("INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
                    "unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')", [s2, direction_fixture])

    result = services.create_lesson_full({
        'lesson_date': '2026-03-20', 'group_id': group_fixture, 'teacher_id': teacher_id_fixture,
        'lesson_number': 1, 'lesson_duration_minutes': 60,
        'attendance': [
            {'student_id': student_fixture, 'present': True},
            {'student_id': s2, 'present': True},
        ],
    })
    lesson_id = result['lesson_id']
    try:
        # До правки: оба платно present, баланс каждого 7 (списан 1).
        assert float(balance_for_student(student_fixture)) == 7.0

        assert repository.update_attendance_cell(
            lesson_id, student_fixture, present=True, is_free=True) is True

        full = services.get_lesson_full(lesson_id)
        # Free оплачивается преподавателю как обычное присутствие (решение
        # 2026-09-28): total=2, present=2 → 500, как было до правки.
        assert full['payroll']['total_students'] == 2
        assert full['payroll']['present_count'] == 2
        assert full['payroll']['payment'] == 500
        # free-ученику баланс возвращается (не списывается по флагу is_free)
        assert float(balance_for_student(student_fixture)) == 8.0
        # платный сосед — по-прежнему списан
        assert float(balance_for_student(s2)) == 7.0
        # прогресс не изменился (present остаётся true)
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')
        with connection.cursor() as cur:
            cur.execute('SELECT present, is_free FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lesson_id, student_fixture])
            present, is_free = cur.fetchone()
        assert present is True and is_free is True
    finally:
        _delete_lesson(lesson_id)
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payments WHERE student_id = %s', [s2])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [s2])
            cur.execute('DELETE FROM students WHERE id = %s', [s2])


def test_attendance_cell_free_needs_no_balance_but_unfree_does(
    group_fixture, teacher_id_fixture, direction_fixture,
):
    """Ученик с НУЛЕВЫМ балансом: «бесплатно» ставится без ошибки (баланс не нужен),
    а возврат в платный present (is_free=false) блокируется UnpaidAttendanceBlocked."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_free_zero__') RETURNING id")
        s = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)", [group_fixture, s])  # без payments → баланс 0

    result = services.create_lesson_full({
        'lesson_date': '2026-03-21', 'group_id': group_fixture, 'teacher_id': teacher_id_fixture,
        'lesson_number': 1, 'lesson_duration_minutes': 60,
        'attendance': [{'student_id': s, 'present': False}],
    })
    lesson_id = result['lesson_id']
    try:
        # free — без баланса, ошибки нет
        assert services.update_attendance_cell(lesson_id, s, present=True, is_free=True) is True
        with connection.cursor() as cur:
            cur.execute('SELECT present, is_free FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lesson_id, s])
            assert cur.fetchone() == (True, True)
        # снять free → платный present, но баланс 0 → блок
        with pytest.raises(UnpaidAttendanceBlocked):
            services.update_attendance_cell(lesson_id, s, present=True, is_free=False)
    finally:
        _delete_lesson(lesson_id)
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [s])
            cur.execute('DELETE FROM students WHERE id = %s', [s])


# ---------------------------------------------------------------------------
# list_lessons
# ---------------------------------------------------------------------------

def test_list_lessons_envelope_and_filter(group_fixture, teacher_id_fixture):
    result = services.create_lesson_full({
        'lesson_date': '2026-03-09',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
    })
    lesson_id = result['lesson_id']
    try:
        list_result = repository.list_lessons(filters={'group_id': group_fixture})
        assert set(list_result.keys()) == {'rows', 'total', 'page', 'page_size'}
        assert list_result['total'] == 1
        assert list_result['rows'][0]['id'] == lesson_id
        assert list_result['rows'][0]['group_name'] == '__les_test_group__'
        assert list_result['rows'][0]['lesson_date'] == datetime.date(2026, 3, 9)
    finally:
        _delete_lesson(lesson_id)


def test_list_lessons_invalid_sort_by_falls_back(group_fixture, teacher_id_fixture):
    # Невалидный sort_by → тихий fallback (как Express paginate), без ошибки.
    result = services.create_lesson_full({
        'lesson_date': '2026-03-10',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
    })
    lesson_id = result['lesson_id']
    try:
        list_result = repository.list_lessons(
            sort_by='; DROP TABLE lessons; --',
            filters={'group_id': group_fixture},
        )
        assert list_result['total'] == 1
    finally:
        _delete_lesson(lesson_id)


def test_record_lesson_payroll_excludes_locked_student_across_rate_bracket(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    """Зарплата считается по составу БЕЗ переведённого ученика — проверка на границе тарифа.

    В группе 3 ученика, один (student_fixture, B=5) заблокирован переводом на уроке №3.
    Правильно: total=2, present=2 → малая группа, все пришли → 500₽.
    Если бы заблокированный протёк в расчёт: total=3, present=3 → 200×3 = 600₽.
    Двух учеников для этой проверки НЕ хватает (1 из 1 и 2 из 2 дают одинаковые 500₽) —
    поэтому именно три.
    """
    extra_students: list[int] = []
    with connection.cursor() as cur:
        for name in ('__les_pay_s2__', '__les_pay_s3__'):
            cur.execute("INSERT INTO students (full_name) "
                        "VALUES (%s) RETURNING id", [name])
            sid = cur.fetchone()[0]
            extra_students.append(sid)
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s, %s, 0, true)", [group_fixture, sid],
            )
            cur.execute(
                "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
                "unit_price, total_amount, paid_at, created_by) "
                "VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')",
                [sid, direction_fixture],
            )

        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__les_pay_locked_src__', %s, %s, false, 60, true, 0) RETURNING id",
            [direction_fixture, teacher_id_fixture],
        )
        src_group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 5, false) RETURNING id", [src_group_id, student_fixture],
        )
        src_membership_id = cur.fetchone()[0]
        cur.execute(
            'UPDATE group_memberships SET transferred_from_id = %s '
            'WHERE group_id = %s AND student_id = %s',
            [src_membership_id, group_fixture, student_fixture],
        )

    result = services.record_lesson(
        lesson_date='2026-03-09', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=3, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test',
        submit_date='2026-03-09',
        attendance=[
            {'student_id': student_fixture, 'present': True},
            {'student_id': extra_students[0], 'present': True},
            {'student_id': extra_students[1], 'present': True},
        ],
    )
    lesson_id = result['lesson_id']
    try:
        full = services.get_lesson_full(lesson_id)
        assert full['payroll']['total_students'] == 2
        assert full['payroll']['present_count'] == 2
        assert full['payroll']['payment'] == 500
        assert result['payment'] == 500
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
            cur.execute('UPDATE group_memberships SET transferred_from_id = NULL '
                        'WHERE group_id = %s AND student_id = %s', [group_fixture, student_fixture])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [src_membership_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [src_group_id])
            for sid in extra_students:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])


def test_record_lesson_free_outcome_no_money_but_progress_and_renewal(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture, lessons_done,
):
    """Бесплатное занятие: у УЧЕНИКА с деньгами ничего (баланс/FIFO не трогаются),
    но преподавателю зарплата за него начисляется как обычно, прогресс идёт (+1) и
    продление двигается (present=true).

    student_fixture (free, баланс 8) + student2 (платно, баланс 8), оба present, 60 мин.
    Ожидаем: payroll total=2/present=2/payment=500 (free оплачивается преподавателю
    как обычное присутствие); баланс free = 8 (не списан), баланс student2 = 7;
    lessons_done обоих = 1; attended_units_total free = 1 (для продления);
    строка free present=true, is_free=true.
    """
    from apps.finances.repository import attended_units_total, balance_for_student

    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_free_s2__') RETURNING id")
        student2_id = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)", [group_fixture, student2_id])
        cur.execute("INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
                    "unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')",
                    [student2_id, direction_fixture])

    assert float(balance_for_student(student_fixture)) == 8.0
    assert float(balance_for_student(student2_id)) == 8.0

    result = services.record_lesson(
        lesson_date='2026-03-10', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test',
        submit_date='2026-03-10',
        attendance=[
            {'student_id': student_fixture, 'present': True, 'is_free': True},
            {'student_id': student2_id, 'present': True},
        ],
    )
    lesson_id = result['lesson_id']
    try:
        full = services.get_lesson_full(lesson_id)
        # Free оплачивается преподавателю как обычное присутствие (решение
        # 2026-09-28): total=2, present=2 → 500, «малая группа, все пришли».
        assert full['payroll']['total_students'] == 2
        assert full['payroll']['present_count'] == 2
        assert full['payroll']['payment'] == 500
        assert result['payment'] == 500
        # деньги УЧЕНИКА: free не списан, платный списан
        assert float(balance_for_student(student_fixture)) == 8.0
        assert float(balance_for_student(student2_id)) == 7.0
        # прогресс: у обоих +1 (free идёт в прогресс курса)
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')
        assert lessons_done(group_fixture, student2_id) == Decimal('1.0')
        # продление: free (present=true) учитывается
        assert attended_units_total(student_fixture) == Decimal('1')
        assert attended_units_total(student2_id) == Decimal('1')
        # строка free: present=true, is_free=true
        with connection.cursor() as cur:
            cur.execute('SELECT present, is_free FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lesson_id, student_fixture])
            present, is_free = cur.fetchone()
        assert present is True and is_free is True
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
            cur.execute('DELETE FROM payments WHERE student_id = %s', [student2_id])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [student2_id])
            cur.execute('DELETE FROM students WHERE id = %s', [student2_id])


def test_record_lesson_solo_free_student_teacher_paid_no_charge(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture, lessons_done,
):
    """Один ученик в группе, бесплатное занятие → преподавателю платят как обычно
    (total=1, present=1 → 500). Баланс УЧЕНИКА не списан, прогресс +1 (present=true)."""
    from apps.finances.repository import balance_for_student

    result = services.record_lesson(
        lesson_date='2026-03-11', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test',
        submit_date='2026-03-11',
        attendance=[{'student_id': student_fixture, 'present': True, 'is_free': True}],
    )
    lesson_id = result['lesson_id']
    try:
        full = services.get_lesson_full(lesson_id)
        assert full['payroll']['total_students'] == 1
        assert full['payroll']['present_count'] == 1
        assert full['payroll']['payment'] == 500
        assert result['payment'] == 500
        assert float(balance_for_student(student_fixture)) == 8.0
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


def test_record_lesson_unpaid_skip_excluded_and_no_pending(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    """Неоплачиваемый пропуск при записи: из зарплаты исключён (present=false),
    pending-резолюция НЕ создаётся (в отличие от обычного «не был»)."""
    from apps.extra_lessons.models import AbsenceResolution

    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_skip_s2__') RETURNING id")
        s2 = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)", [group_fixture, s2])
        cur.execute("INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
                    "unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')", [s2, direction_fixture])

    result = services.record_lesson(
        lesson_date='2026-03-12', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test', submit_date='2026-03-12',
        attendance=[
            {'student_id': student_fixture, 'present': False, 'unpaid_skip': True},
            {'student_id': s2, 'present': True},
        ],
    )
    lid = result['lesson_id']
    try:
        full = services.get_lesson_full(lid)
        assert full['payroll']['total_students'] == 1  # skip исключён из headcount
        assert full['payroll']['present_count'] == 1
        assert full['payroll']['payment'] == 500
        # неоплачиваемый пропуск НЕ порождает pending
        assert not AbsenceResolution.objects.filter(
            missed_lesson_id=lid, student_id=student_fixture).exists()
        with connection.cursor() as cur:
            cur.execute('SELECT present, unpaid_skip FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lid, student_fixture])
            assert cur.fetchone() == (False, True)
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
            cur.execute('DELETE FROM payments WHERE student_id = %s', [s2])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [s2])
            cur.execute('DELETE FROM students WHERE id = %s', [s2])


def test_set_unpaid_skip_on_recorded_lesson_for_newly_added_student(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    """Ключевой сценарий: урок УЖЕ проведён (1 ученик, 500₽). Потом в группу добавлен
    новый ученик (перевод/начал не с 1-го). Ему ставят «неоплачиваемый пропуск» на
    этот прошлый урок — строка создаётся задним числом, из зарплаты он исключён
    (payroll не меняется), pending нет. Снятие удаляет строку."""
    result = services.record_lesson(
        lesson_date='2026-03-13', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test', submit_date='2026-03-13',
        attendance=[{'student_id': student_fixture, 'present': True}],
    )
    lid = result['lesson_id']
    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_skip_new__') RETURNING id")
        s_new = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)", [group_fixture, s_new])
    try:
        assert services.set_unpaid_skip(lid, s_new, True) is True
        full = services.get_lesson_full(lid)
        assert full['payroll']['total_students'] == 1   # новый исключён
        assert full['payroll']['present_count'] == 1
        assert full['payroll']['payment'] == 500
        with connection.cursor() as cur:
            cur.execute('SELECT present, unpaid_skip FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lid, s_new])
            assert cur.fetchone() == (False, True)

        # снятие → строка удаляется
        assert services.set_unpaid_skip(lid, s_new, False) is True
        with connection.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lid, s_new])
            assert cur.fetchone()[0] == 0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [s_new])
            cur.execute('DELETE FROM students WHERE id = %s', [s_new])


def test_set_unpaid_skip_missing_lesson_returns_false():
    assert services.set_unpaid_skip(999_999_999, 1, True) is False


def test_lesson_skip_on_future_slot_then_recorded_excludes_student(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    """Вариант A: пометка «неопл. пропуск» на слот, которого ЕЩЁ НЕ БЫЛО (урока нет),
    ставится без даты. Когда группа проводит этот урок — помеченный ученик исключён
    из зарплаты (present=false, unpaid_skip=true), pending нет."""
    from apps.extra_lessons.models import AbsenceResolution

    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_lsk_s2__') RETURNING id")
        s2 = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)", [group_fixture, s2])
        cur.execute("INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
                    "unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')", [s2, direction_fixture])

    services.set_lesson_skip(group_fixture, student_fixture, 1, True)
    assert services.list_lesson_skips(group_fixture, 1) == [student_fixture]

    # группа проводит урок №1: student_fixture приходит в payload как present, но
    # слот-маркер форсит его в неопл. пропуск.
    result = services.record_lesson(
        lesson_date='2026-03-14', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test', submit_date='2026-03-14',
        attendance=[
            {'student_id': student_fixture, 'present': True},
            {'student_id': s2, 'present': True},
        ],
    )
    lid = result['lesson_id']
    try:
        full = services.get_lesson_full(lid)
        assert full['payroll']['total_students'] == 1
        assert full['payroll']['payment'] == 500
        assert not AbsenceResolution.objects.filter(
            missed_lesson_id=lid, student_id=student_fixture).exists()
        with connection.cursor() as cur:
            cur.execute('SELECT present, unpaid_skip FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lid, student_fixture])
            assert cur.fetchone() == (False, True)
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [group_fixture])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
            cur.execute('DELETE FROM payments WHERE student_id = %s', [s2])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [s2])
            cur.execute('DELETE FROM students WHERE id = %s', [s2])


def test_lesson_skip_on_recorded_slot_materializes_and_unset(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture,
):
    """Урок №1 уже проведён (student_fixture present, 500₽). Ставим пометку на ЭТОТ
    слот новому ученику → материализуется в lesson_attendance (payroll не меняется).
    Снятие удаляет маркер и строку."""
    result = services.record_lesson(
        lesson_date='2026-03-15', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test', submit_date='2026-03-15',
        attendance=[{'student_id': student_fixture, 'present': True}],
    )
    lid = result['lesson_id']
    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_lsk_new__') RETURNING id")
        s_new = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)", [group_fixture, s_new])
    try:
        services.set_lesson_skip(group_fixture, s_new, 1, True)
        full = services.get_lesson_full(lid)
        assert full['payroll']['total_students'] == 1  # не меняется, s_new исключён
        assert full['payroll']['payment'] == 500
        with connection.cursor() as cur:
            cur.execute('SELECT present, unpaid_skip FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lid, s_new])
            assert cur.fetchone() == (False, True)

        services.set_lesson_skip(group_fixture, s_new, 1, False)
        assert services.list_lesson_skips(group_fixture, 1) == []
        with connection.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM lesson_attendance '
                        'WHERE lesson_id = %s AND student_id = %s', [lid, s_new])
            assert cur.fetchone()[0] == 0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [group_fixture])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [s_new])
            cur.execute('DELETE FROM students WHERE id = %s', [s_new])


def test_record_lesson_silently_excludes_locked_students(
    group_fixture, teacher_id_fixture, student_fixture, direction_fixture, membership_fixture, lessons_done,
):
    """B=5 для student_fixture; урок №3 (locked) исключает его из attendance/total_students,
    но обычный ученик той же группы (student2) отмечается нормально."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) "
                    "VALUES ('__les_locked_s2__') RETURNING id")
        student2_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true) RETURNING id",
            [group_fixture, student2_id],
        )
        membership2_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test') RETURNING id",
            [student2_id, direction_fixture],
        )
        payment2_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) VALUES ('__les_rl_locked_src__', %s, %s, false, 60, true, 0) "
            "RETURNING id",
            [direction_fixture, teacher_id_fixture],
        )
        src_group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 5, false) RETURNING id",
            [src_group_id, student_fixture],
        )
        src_membership_id = cur.fetchone()[0]
        cur.execute(
            'UPDATE group_memberships SET transferred_from_id = %s '
            'WHERE group_id = %s AND student_id = %s',
            [src_membership_id, group_fixture, student_fixture],
        )

    result = services.record_lesson(
        lesson_date='2026-03-08', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=3, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='test',
        submit_date='2026-03-08',
        attendance=[
            {'student_id': student_fixture, 'present': True},
            {'student_id': student2_id, 'present': True},
        ],
    )
    lesson_id = result['lesson_id']
    try:
        full = services.get_lesson_full(lesson_id)
        student_ids_in_attendance = {a['student_id'] for a in full['attendance']}
        assert student_fixture not in student_ids_in_attendance
        assert student2_id in student_ids_in_attendance
        assert full['payroll']['total_students'] == 1
        assert full['payroll']['present_count'] == 1
        assert lessons_done(group_fixture, student_fixture) == Decimal('0.0')
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
            cur.execute('UPDATE group_memberships SET transferred_from_id = NULL '
                        'WHERE group_id = %s AND student_id = %s', [group_fixture, student_fixture])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [src_membership_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [src_group_id])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership2_id])
            cur.execute('DELETE FROM payments WHERE id = %s', [payment2_id])
            cur.execute('DELETE FROM students WHERE id = %s', [student2_id])
