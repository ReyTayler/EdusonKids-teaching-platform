"""
Генератор выдуманной «минимальной школы» для тестов (форк Eduson).

Тесты групп/членств/оплат берут «любую» строку из общей тестовой БД. В KOTOKOD
она приезжала с данными разработки, а eduson_test строится миграциями пустой, и
такие тесты молча пропускались. Генератор подставляет минимум — проверяем, что
он даёт ровно то, что эти тесты ищут, не дублирует себя и не несёт похожих на
реальные ПДн значений.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.core.tests.minimal_school import ensure_minimal_school


def _scalar(sql: str, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchone()[0]


@pytest.mark.django_db
def test_creates_rows_that_previously_skipped_tests_look_for():
    ids = ensure_minimal_school()

    # Ровно те запросы, по пустому результату которых тесты делали pytest.skip.
    assert _scalar('SELECT count(*) FROM directions WHERE id = %s', [ids['direction_id']]) == 1
    assert _scalar('SELECT count(*) FROM teachers WHERE id = %s', [ids['teacher_id']]) == 1
    assert _scalar(
        'SELECT count(*) FROM groups WHERE id = %s AND active = true', [ids['group_id']],
    ) == 1
    assert _scalar('SELECT count(*) FROM group_schedule_slots WHERE group_id = %s',
                   [ids['group_id']]) >= 1
    assert _scalar('SELECT count(*) FROM students WHERE id = %s', [ids['student_id']]) == 1
    assert _scalar(
        'SELECT count(*) FROM payments WHERE student_id = %s AND direction_id IS NOT NULL',
        [ids['student_id']],
    ) == 1
    assert _scalar(
        'SELECT count(*) FROM lesson_attendance WHERE student_id = %s AND present = true',
        [ids['student_id']],
    ) == 1
    # Посещение — не «посещение без членства»: ученик активен в группе урока.
    assert _scalar(
        'SELECT count(*) FROM group_memberships WHERE group_id = %s AND student_id = %s '
        'AND active = true', [ids['group_id'], ids['student_id']],
    ) == 1


@pytest.mark.django_db
def test_free_student_has_no_membership_nor_payments():
    # Под тесты, которые сами создают пару «группа + ученик» и ждут 201.
    ids = ensure_minimal_school()
    assert ids['free_student_id'] != ids['student_id']
    for table in ('group_memberships', 'payments', 'lesson_attendance'):
        assert _scalar(
            f'SELECT count(*) FROM {table} WHERE student_id = %s',  # литерал теста
            [ids['free_student_id']],
        ) == 0


@pytest.mark.django_db
def test_payment_respects_money_invariants():
    ids = ensure_minimal_school()
    with connection.cursor() as cur:
        cur.execute(
            'SELECT kind, unit_price, subscriptions_count, total_amount, lessons_count '
            'FROM payments WHERE student_id = %s', [ids['student_id']],
        )
        kind, unit_price, subs, total, lessons = cur.fetchone()
    assert kind == 'purchase'
    assert total == unit_price * subs
    assert lessons > 0


@pytest.mark.django_db
def test_is_idempotent():
    first = ensure_minimal_school()
    counts = [
        _scalar(f'SELECT count(*) FROM {table}')  # имена таблиц — литералы теста
        for table in ('directions', 'teachers', 'groups', 'group_schedule_slots', 'students',
                      'group_memberships', 'lessons', 'lesson_attendance', 'payments')
    ]
    second = ensure_minimal_school()
    assert second == first
    assert counts == [
        _scalar(f'SELECT count(*) FROM {table}')
        for table in ('directions', 'teachers', 'groups', 'group_schedule_slots', 'students',
                      'group_memberships', 'lessons', 'lesson_attendance', 'payments')
    ]


@pytest.mark.django_db
def test_values_are_obviously_fake():
    ids = ensure_minimal_school()
    with connection.cursor() as cur:
        cur.execute(
            'SELECT full_name, parent1_phone, parent1_email FROM students WHERE id = %s',
            [ids['student_id']],
        )
        name, phone, email = cur.fetchone()
    assert name.startswith('Тест ')
    assert phone.startswith('7000000000')
    assert email.endswith('@example.com')
