"""
Остаток реестра Eduson — остаток КУРСА: журнал посещений и оплаты его не двигают.

Раньше реестр считал остаток денег («куплено − пройдено» по lesson_attendance),
и здесь стояла регрессия прода KOTOKOD 12.08.2026: бесплатное занятие
списывало остаток. В Eduson оплат нет (спека этапа 3, раздел B), и остаток =
длина курса группы − пройдено (lessons_done + неоплачиваемые пропуски).
lessons_done ведёт apps.lessons.services.record_lesson; строки посещаемости
сами по себе — бесплатные, доп.уроки, 45-минутные — остаток не меняют, иначе
одно занятие учлось бы дважды. Денежный инвариант бесплатного занятия живёт
в apps.finances и проверяется там.

Дата последнего занятия, наоборот, читается из посещаемости: бесплатное
занятие — реальное присутствие, оно обязано снимать «простой».
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.dashboard import registry_service as svc

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def graph():
    """Direction → teacher → group → student → membership (active)."""
    created: dict[str, list[int]] = {
        'directions': [], 'teachers': [], 'groups': [], 'students': [], 'memberships': [],
    }
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__reg_free_dir__', 16, true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        created['directions'].append(direction_id)

        cur.execute("INSERT INTO teachers (name) VALUES ('__reg_free_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        created['teachers'].append(teacher_id)

        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__reg_free_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]
        created['groups'].append(group_id)

        cur.execute(
            "INSERT INTO students (full_name) VALUES ('__reg_free_student__') RETURNING id"
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


def _add_lesson(graph, *, lesson_date, lesson_number, present=True, is_free=False, duration=60):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,%s,%s,%s,'regular','test') RETURNING id",
            [graph['group_id'], graph['teacher_id'], lesson_date, lesson_number, duration],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
            'VALUES (%s,%s,%s,%s)',
            [lesson_id, graph['student_id'], present, is_free],
        )
    return lesson_id


def _list_balance(graph):
    return svc.base_students_qs(TODAY).get(pk=graph['student_id']).balance


def _summary_row(graph):
    rows = {r['student_id']: r for r in svc._summary_rows(TODAY)}
    return rows[graph['student_id']]


def test_attendance_rows_and_payments_do_not_move_course_left(graph):
    """Остаток = 16 (программа направления) − lessons_done (0): что бы ни лежало в журнале."""
    assert _list_balance(graph) == 16
    _add_payment(graph, 4, 8000)
    _add_lesson(graph, lesson_date='2026-06-05', lesson_number=1)
    _add_lesson(graph, lesson_date='2026-06-10', lesson_number=2, is_free=True)
    _add_lesson(graph, lesson_date='2026-06-11', lesson_number=3, is_free=True, duration=45)

    assert _list_balance(graph) == 16
    assert _summary_row(graph)['balance'] == 16


def test_course_left_follows_lessons_done(graph):
    """Прогресс курса двигает именно счётчик членства (его ведёт record_lesson)."""
    with connection.cursor() as cur:
        cur.execute('UPDATE group_memberships SET lessons_done = 3.5 WHERE student_id = %s',
                    [graph['student_id']])
    assert _list_balance(graph) == 12.5
    assert _summary_row(graph)['balance'] == 12.5


def test_free_lesson_still_counts_as_activity(graph):
    """
    Бесплатное занятие — реальное присутствие: дата последнего занятия обязана
    двигаться, иначе ученик ложно попадёт в «простой». Тот же водораздел, что
    у доп.урока (_last_lesson_subquery).
    """
    _add_lesson(graph, lesson_date='2026-06-10', lesson_number=1, is_free=True)

    assert svc.base_students_qs(TODAY).get(pk=graph['student_id']).last_lesson ==         datetime.date(2026, 6, 10)
    assert _summary_row(graph)['last_lesson'] == datetime.date(2026, 6, 10)
