"""
Регрессия (code review, доп.уроки): apps.dashboard.registry_service.base_students_qs
не должен двойно списывать баланс за компенсированный пропуск — тот же
инвариант, что apps.finances.repository (см. test_extra_lesson_does_not_double_count_balance),
но для отдельного subquery-пути реестра куратора (_attended_units_subquery).
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.dashboard import registry_service as svc

pytestmark = pytest.mark.django_db


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def graph():
    """Direction → teacher → group → student → membership (enrolled, active)."""
    created: dict[str, list[int]] = {
        'directions': [], 'teachers': [], 'groups': [], 'students': [],
        'memberships': [], 'payments': [], 'lessons': [],
    }
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__reg_bal_dir__', 16, true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        created['directions'].append(direction_id)

        cur.execute("INSERT INTO teachers (name) VALUES ('__reg_bal_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        created['teachers'].append(teacher_id)

        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__reg_bal_group__', %s, %s, false, 60, true, 0) "
            "RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]
        created['groups'].append(group_id)

        cur.execute(
            "INSERT INTO students (full_name) "
            "VALUES ('__reg_bal_student__') RETURNING id"
        )
        student_id = cur.fetchone()[0]
        created['students'].append(student_id)

        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true) RETURNING id",
            [group_id, student_id],
        )
        created['memberships'].append(cur.fetchone()[0])

    yield {
        'direction_id': direction_id, 'teacher_id': teacher_id,
        'group_id': group_id, 'student_id': student_id,
    }

    with connection.cursor() as cur:
        # Урок и attendance удаляем по group_id (изолированная тестовая группа),
        # а не по трекингу id из _add_lesson — надёжнее, не требует, чтобы
        # каждый вызов _add_lesson регистрировал id в created['lessons'].
        cur.execute('DELETE FROM payroll WHERE lesson_id IN '
                    '(SELECT id FROM lessons WHERE group_id = %s)', [group_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id IN '
                    '(SELECT id FROM lessons WHERE group_id = %s)', [group_id])
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM payments WHERE student_id = ANY(%s)', [created['students']])
        for mid in created['memberships']:
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [mid])
        for sid in created['students']:
            cur.execute('DELETE FROM students WHERE id = %s', [sid])
        for gid in created['groups']:
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])
        for tid in created['teachers']:
            cur.execute('DELETE FROM teachers WHERE id = %s', [tid])
        for did in created['directions']:
            cur.execute('DELETE FROM directions WHERE id = %s', [did])


def _add_payment(graph, lessons_count, amount):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,'2026-06-01','test') RETURNING id",
            [graph['student_id'], graph['direction_id'], 1, lessons_count,
             amount // lessons_count, amount],
        )
        return cur.fetchone()[0]


def _add_lesson(graph, *, lesson_date, lesson_type, present, lesson_number=1, duration=60):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,%s,%s,%s,%s,'test') RETURNING id",
            [graph['group_id'], graph['teacher_id'], lesson_date, lesson_number, duration, lesson_type],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,%s)',
            [lesson_id, graph['student_id'], present],
        )
    return lesson_id


def test_extra_lesson_counts_once_in_dashboard_balance(graph):
    """Модель 1c: потребление от факта доп.урока (extra, present=true); исходный
    пропуск остаётся present=false. Списывается ровно 1 (extra), не два."""
    _add_payment(graph, 4, 8000)  # 4 куплено

    _add_lesson(graph, lesson_date='2026-06-05', lesson_type='regular', present=False)
    today = datetime.date(2026, 6, 15)
    row = svc.base_students_qs(today).get(pk=graph['student_id'])
    assert row.balance == 4  # пропуск ещё не компенсирован

    # Доп.урок: своя present=True строка — ЕДИНСТВЕННЫЙ источник потребления
    # (исходный урок не флипаем, модель 1c).
    _add_lesson(graph, lesson_date='2026-06-10', lesson_type='extra', present=True)

    row = svc.base_students_qs(today).get(pk=graph['student_id'])
    assert row.balance == 3  # ровно один урок списан (extra), не два
