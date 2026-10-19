"""
«Неоплачиваемый пропуск» двигает прогресс курса в «Реестре куратора».

Сценарий из жизни: ученик прошёл 14 уроков в группе 001, перешёл в 002 и учится
там с 15-го. Первые 14 уроков в 002 ему ставят «неоплачиваемый пропуск»
(терминальный исход «этот урок он прошёл в другом месте»). Прогресс реестра
считался только по group_memberships.lessons_done — а он растёт лишь на
present=true, — и показывал 0 из 16.

Пропуск живёт в двух местах (см. apps.lessons.models):
  • lesson_skips — пометка на СЛОТ группы, в т.ч. на ещё не проведённый урок;
  • lesson_attendance.unpaid_skip — исход на проведённом уроке (ставится и
    точечно, без пометки на слоте).
Один и тот же слот, отмеченный в обоих местах, считается один раз.
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
    """Направление (16 уроков) → две группы (60 и 45 мин) → ученик, активен в первой."""
    ids: dict[str, list[int]] = {'groups': [], 'memberships': []}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__reg_skip_dir__', 16, true) RETURNING id")
        direction_id = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__reg_skip_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        for name, duration in (('__reg_skip_g60__', 60), ('__reg_skip_g45__', 45)):
            cur.execute(
                "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
                "lesson_duration_minutes, active, lesson_number_offset) "
                "VALUES (%s, %s, %s, false, %s, true, 0) RETURNING id",
                [name, direction_id, teacher_id, duration],
            )
            ids['groups'].append(cur.fetchone()[0])
        cur.execute("INSERT INTO students (full_name) VALUES ('__reg_skip_student__') RETURNING id")
        student_id = cur.fetchone()[0]

    g = {
        'direction_id': direction_id, 'teacher_id': teacher_id, 'student_id': student_id,
        'g60': ids['groups'][0], 'g45': ids['groups'][1], '_ids': ids,
    }
    yield g

    with connection.cursor() as cur:
        group_ids = ids['groups']
        cur.execute('DELETE FROM lesson_skips WHERE group_id = ANY(%s)', [group_ids])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id IN '
                    '(SELECT id FROM lessons WHERE group_id = ANY(%s))', [group_ids])
        cur.execute('DELETE FROM lessons WHERE group_id = ANY(%s)', [group_ids])
        cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [student_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])
        cur.execute('DELETE FROM groups WHERE id = ANY(%s)', [group_ids])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


def _membership(g, group_id, *, lessons_done=0, active=True):
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, %s, %s)',
            [group_id, g['student_id'], lessons_done, active],
        )


def _slot_skip(g, group_id, lesson_number):
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) '
            'VALUES (%s, %s, %s, now())',
            [group_id, g['student_id'], lesson_number],
        )


def _lesson_skip(g, group_id, lesson_number, *, duration=60, lesson_type='regular'):
    """Проведённый урок, на котором ученику стоит исход unpaid_skip."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'test') RETURNING id",
            [group_id, g['teacher_id'],
             datetime.date(2026, 5, 1) + datetime.timedelta(days=int(lesson_number * 2)),
             lesson_number, duration, lesson_type],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, unpaid_skip) '
            'VALUES (%s, %s, false, true)',
            [lesson_id, g['student_id']],
        )


def _list_attended(g):
    return svc.base_students_qs(TODAY).get(pk=g['student_id']).attended


def _summary_attended(g):
    rows = {r['student_id']: r for r in svc._summary_rows(TODAY)}
    return rows[g['student_id']]['attended']


def _assert_attended(g, expected):
    assert _list_attended(g) == expected
    assert _summary_attended(g) == expected


def test_transfer_example_slot_skips_count_as_progress(graph):
    """Пример юзера: 14 уроков в 001 (членство снято), в 002 — 14 пропусков + 1 урок."""
    _membership(graph, graph['g45'], lessons_done=14, active=False)   # старая группа
    _membership(graph, graph['g60'], lessons_done=1)                  # учится с 15-го
    for n in range(1, 15):
        _slot_skip(graph, graph['g60'], n)

    _assert_attended(graph, 15)
    row = svc.serialize_rows([svc.base_students_qs(TODAY).get(pk=graph['student_id'])])[0]
    assert (row['attended'], row['planned'], row['progress_pct']) == (15, 16, 94)


def test_attendance_unpaid_skip_without_slot_marker_counts(graph):
    """Исход, поставленный точечно на проведённом уроке, без пометки на слоте."""
    _membership(graph, graph['g60'], lessons_done=2)
    _lesson_skip(graph, graph['g60'], 1)
    _lesson_skip(graph, graph['g60'], 2)

    _assert_attended(graph, 4)


def test_slot_marked_in_both_places_counts_once(graph):
    """Пометка на слоте материализуется в lesson_attendance — один слот, один урок."""
    _membership(graph, graph['g60'])
    _slot_skip(graph, graph['g60'], 1)
    _lesson_skip(graph, graph['g60'], 1)
    _lesson_skip(graph, graph['g60'], 2)   # только в посещаемости
    _slot_skip(graph, graph['g60'], 3)     # только на слоте (урок ещё не проведён)

    _assert_attended(graph, 3)


def test_half_lesson_group_skip_weighs_half(graph):
    """45-мин группа: слот = 0.5 урока, как lessons_done (шаг 0.5)."""
    _membership(graph, graph['g45'], lessons_done=1)
    _slot_skip(graph, graph['g45'], 0.5)
    _slot_skip(graph, graph['g45'], 1)
    _lesson_skip(graph, graph['g45'], 1.5, duration=45)

    _assert_attended(graph, 2.5)


def test_skips_in_inactive_membership_do_not_count(graph):
    """Прогресс — по активным членствам: пропуски покинутой группы не в счёт."""
    _membership(graph, graph['g60'], lessons_done=3)
    _membership(graph, graph['g45'], active=False)
    _slot_skip(graph, graph['g45'], 1)
    _lesson_skip(graph, graph['g45'], 2, duration=45)

    _assert_attended(graph, 3)


def test_skips_within_transfer_offset_not_double_counted(graph):
    """
    Группа-продолжение перевода: lessons_done ученика уже засеян значением B
    (= lesson_number_offset), слоты ≤ B в плане отсутствуют. Пропуск на таком
    слоте не должен лечь поверх засеянного значения.
    """
    with connection.cursor() as cur:
        cur.execute('UPDATE groups SET lesson_number_offset = 5 WHERE id = %s', [graph['g60']])
    _membership(graph, graph['g60'], lessons_done=5)
    _slot_skip(graph, graph['g60'], 3)
    _slot_skip(graph, graph['g60'], 6)

    _assert_attended(graph, 6)


def test_progress_sort_uses_skips(graph):
    """Сортировка по прогрессу — в SQL, поэтому пропуски обязаны быть в аннотации."""
    _membership(graph, graph['g60'])
    for n in range(1, 9):
        _slot_skip(graph, graph['g60'], n)

    st = svc.base_students_qs(TODAY).get(pk=graph['student_id'])
    assert st.progress_ratio == pytest.approx(0.5)
