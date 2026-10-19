"""
Регрессия: «бесплатное занятие» не списывает остаток в «Реестре куратора».

Исход «Бесплатное занятие» (lesson_attendance.is_free) — присутствие без денег:
present=true, но баланс ученика не трогается и партии оплат не гасятся
(apps/finances/repository.py::balances_for_students фильтрует is_free=False).
Реестр считает остаток СВОЕЙ формулой — коррелированным подзапросом в списке
(_attended_units_subquery) и пакетно в сводке (_summary_rows), — и оба места
фильтр по is_free потеряли: бесплатное занятие списывалось как обычное.

Прод 12.08.2026: у ученика с двумя бесплатными занятиями карточка показывала
остаток 2, реестр — 0, и куратору падала ложная задача «абонемент закончился».

Главный тест здесь — сверка с apps.finances (test_registry_balance_matches_finances):
остаток в реестре обязан совпадать с каноническим до последнего знака, какими бы
исходами ни был набран журнал занятий.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.dashboard import registry_service as svc
from apps.finances.repository import balances_for_students

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


def test_free_lesson_does_not_consume_balance_in_list(graph):
    """Список реестра: бесплатное занятие проведено, остаток не изменился."""
    _add_payment(graph, 4, 8000)
    _add_lesson(graph, lesson_date='2026-06-05', lesson_number=1)          # платное
    assert _list_balance(graph) == 3

    _add_lesson(graph, lesson_date='2026-06-10', lesson_number=2, is_free=True)
    assert _list_balance(graph) == 3, 'бесплатное занятие списало остаток'


def test_free_lesson_does_not_consume_balance_in_summary(graph):
    """Пакетный путь сводки — тот же инвариант (KPI «Уроков впереди», сигналы)."""
    _add_payment(graph, 4, 8000)
    _add_lesson(graph, lesson_date='2026-06-05', lesson_number=1)
    _add_lesson(graph, lesson_date='2026-06-10', lesson_number=2, is_free=True)

    assert _summary_row(graph)['balance'] == 3


def test_free_lesson_still_counts_as_activity(graph):
    """
    Из БАЛАНСА бесплатное занятие исключено, но ученик на нём был: дата
    последнего занятия обязана двигаться, иначе он ложно попадёт в «простой».
    Тот же водораздел, что у доп.урока (_last_lesson_subquery).
    """
    _add_payment(graph, 4, 8000)
    _add_lesson(graph, lesson_date='2026-06-10', lesson_number=1, is_free=True)

    assert svc.base_students_qs(TODAY).get(pk=graph['student_id']).last_lesson == \
        datetime.date(2026, 6, 10)
    assert _summary_row(graph)['last_lesson'] == datetime.date(2026, 6, 10)


def test_half_free_lesson_does_not_consume_balance(graph):
    """45-минутное бесплатное занятие не списывает и половины урока."""
    _add_payment(graph, 4, 8000)
    _add_lesson(graph, lesson_date='2026-06-10', lesson_number=1, is_free=True, duration=45)

    assert _list_balance(graph) == 4
    assert _summary_row(graph)['balance'] == 4


def test_registry_balance_matches_finances(graph):
    """
    Остаток реестра == канонический остаток (apps.finances) на журнале из всех
    исходов сразу. Тест, ради которого написан файл: формулы живут в трёх местах
    и разъезжаться не должны.
    """
    _add_payment(graph, 10, 20000)
    _add_lesson(graph, lesson_date='2026-06-01', lesson_number=1)                     # −1
    _add_lesson(graph, lesson_date='2026-06-02', lesson_number=2, duration=45)        # −0.5
    _add_lesson(graph, lesson_date='2026-06-03', lesson_number=3, present=False)      # 0
    _add_lesson(graph, lesson_date='2026-06-04', lesson_number=4, is_free=True)       # 0
    _add_lesson(graph, lesson_date='2026-06-05', lesson_number=5, is_free=True,
                duration=45)                                                          # 0

    canonical = balances_for_students([graph['student_id']])[graph['student_id']]
    assert canonical == 85 / 10                       # 10 − 1 − 0.5
    assert _list_balance(graph) == canonical
    assert _summary_row(graph)['balance'] == canonical
