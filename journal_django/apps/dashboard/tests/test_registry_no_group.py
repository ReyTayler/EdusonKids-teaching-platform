"""
Режим «Без группы» в реестре куратора.

Реестр показывает учеников с активным членством в активной группе. Оплаченный
ученик, которого ещё не распределили (или чью группу заархивировали), не виден
нигде: dev-БД 12.08.2026 — 57 таких учеников, у 33 положительный остаток.

`no_group=True` переворачивает предикат популяции: тот же queryset, те же
колонки и статусы, только Exists → NOT Exists. Отсюда главный инвариант, ради
которого написан файл: два режима разбивают учеников школы БЕЗ пересечения и
БЕЗ потерь (test_modes_partition_all_students). Если предикаты разъедутся,
ученик либо задвоится, либо исчезнет из обоих списков.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.dashboard import registry_service as svc
from apps.students.models import Student

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def graph():
    """
    Четыре ученика, покрывающие все способы остаться без группы:
      in_group  — активное членство в активной группе (обычный реестр);
      archived  — членство активно, но группа заархивирована;
      dropped   — группа активна, но членство снято;
      never     — членств нет вообще (новенький).
    У `never` есть оплата и посещение прошлого курса — проверяем, что
    аннотации остатка и даты последнего занятия работают и без членства.
    """
    ids: dict[str, list[int]] = {'dirs': [], 'teachers': [], 'groups': [], 'students': []}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__ng_dir__', 16, true) RETURNING id")
        direction_id = cur.fetchone()[0]
        ids['dirs'].append(direction_id)

        cur.execute("INSERT INTO teachers (name) VALUES ('__ng_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        ids['teachers'].append(teacher_id)

        # Активная и заархивированная группы.
        for name, active in (('__ng_group_active__', True), ('__ng_group_archived__', False)):
            cur.execute(
                'INSERT INTO groups (name, direction_id, teacher_id, is_individual, '
                'lesson_duration_minutes, active, lesson_number_offset) '
                'VALUES (%s, %s, %s, false, 60, %s, 0) RETURNING id',
                [name, direction_id, teacher_id, active],
            )
            ids['groups'].append(cur.fetchone()[0])
        g_active, g_archived = ids['groups']

        students: dict[str, int] = {}
        for key in ('in_group', 'archived', 'dropped', 'never'):
            cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id',
                        [f'__ng_{key}__'])
            students[key] = cur.fetchone()[0]
            ids['students'].append(students[key])

        for key, group_id, active in (
            ('in_group', g_active, True),
            ('archived', g_archived, True),
            ('dropped', g_active, False),
        ):
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s, %s, 0, %s)', [group_id, students[key], active],
            )

        # `never`: оплата 8 уроков и одно посещение 60 мин — остаток 7.
        cur.execute(
            'INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, '
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s, %s, 1, 8, 1000, 8000, '2026-06-01', 'test')",
            [students['never'], direction_id],
        )
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-06-04', 1, 60, 'regular', 'test') RETURNING id",
            [g_archived, teacher_id],
        )
        cur.execute('INSERT INTO lesson_attendance (lesson_id, student_id, present) '
                    'VALUES (%s, %s, true)', [cur.fetchone()[0], students['never']])

    yield students

    with connection.cursor() as cur:
        groups = ids['groups']
        cur.execute('DELETE FROM payroll WHERE lesson_id IN '
                    '(SELECT id FROM lessons WHERE group_id = ANY(%s))', [groups])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id IN '
                    '(SELECT id FROM lessons WHERE group_id = ANY(%s))', [groups])
        cur.execute('DELETE FROM lessons WHERE group_id = ANY(%s)', [groups])
        cur.execute('DELETE FROM payments WHERE student_id = ANY(%s)', [ids['students']])
        cur.execute('DELETE FROM group_memberships WHERE student_id = ANY(%s)', [ids['students']])
        cur.execute('DELETE FROM students WHERE id = ANY(%s)', [ids['students']])
        cur.execute('DELETE FROM groups WHERE id = ANY(%s)', [groups])
        cur.execute('DELETE FROM teachers WHERE id = ANY(%s)', [ids['teachers']])
        cur.execute('DELETE FROM directions WHERE id = ANY(%s)', [ids['dirs']])


def _ids(qs) -> set[int]:
    return set(qs.values_list('pk', flat=True))


def test_default_mode_keeps_only_students_in_groups(graph):
    got = _ids(svc.base_students_qs(TODAY))
    assert graph['in_group'] in got
    for key in ('archived', 'dropped', 'never'):
        assert graph[key] not in got, f'{key} не должен попадать в обычный реестр'


def test_no_group_mode_collects_everyone_else(graph):
    got = _ids(svc.base_students_qs(TODAY, no_group=True))
    for key in ('archived', 'dropped', 'never'):
        assert graph[key] in got, f'{key} должен попадать в режим «Без группы»'
    assert graph['in_group'] not in got


def test_modes_partition_all_students(graph):
    """Разбиение без пересечений и без потерь — по всей таблице учеников."""
    with_group = _ids(svc.base_students_qs(TODAY))
    without = _ids(svc.base_students_qs(TODAY, no_group=True))

    assert with_group & without == set(), 'ученик попал в оба режима'
    assert with_group | without == set(Student.objects.values_list('pk', flat=True))


def test_no_group_row_keeps_balance_and_last_lesson(graph):
    """
    Колонки те же, значит и считаться должны так же: у безгруппного ученика
    остаток и дата последнего занятия берутся из оплат и журнала, а не из членств.
    """
    row = svc.base_students_qs(TODAY, no_group=True).get(pk=graph['never'])
    assert row.balance == 7                       # 8 куплено − 1 проведён
    assert row.last_lesson == datetime.date(2026, 6, 4)
    assert row.next_lesson is None                # плановых занятий без группы нет
    assert row.planned == 0                       # прогресс берётся из членств
    assert row.attended == 0


def test_no_group_rows_serialize_with_same_shape(graph):
    """
    serialize_rows не должен спотыкаться на пустых кодах/преподавателях.

    Берём `dropped` (ни оплат, ни занятий): его статус — «Закрыт» по нулевому
    остатку, и это не зависит от даты прогона. У `never` статус плавал бы между
    «Нет плана» и «Простой», потому что serialize_rows считает его от реального
    сегодня, а не от TODAY фикстуры.
    """
    students = list(svc.base_students_qs(TODAY, no_group=True).filter(pk=graph['dropped']))
    (row,) = svc.serialize_rows(students)

    assert row['student_id'] == graph['dropped']
    assert row['codes'] == []
    assert row['teacher_names'] == []
    assert row['progress_pct'] is None
    assert row['balance'] == 0
    assert row['status'] == 'closed'


def test_students_qs_passes_no_group_through(graph):
    """Публичная точка входа (её зовёт view) — фильтр, поиск и сортировка живы."""
    got = _ids(svc.students_qs(no_group=True, search='__ng_'))
    assert got == {graph['archived'], graph['dropped'], graph['never']}

    ordered = list(
        svc.students_qs(no_group=True, search='__ng_', sort_by='balance', sort_dir='desc')
        .values_list('pk', flat=True)
    )
    assert ordered[0] == graph['never'], 'сортировка по остатку не применилась'
