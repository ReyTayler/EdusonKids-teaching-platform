"""
Сводка реестра считает те же поля, что и список, но пакетно — сверка путей.

`base_students_qs` (список) считает balance/attended/planned/last/next/cancellations
коррелированными подзапросами: по одному на ученика. Для страницы из 50 строк
это дёшево, а сводке нужна вся активная популяция, и подзапрос «дата последнего
занятия» вырождался в перебор журнала занятий с конца отдельно на каждого
ученика. Замер 2026-08-02 на 236 учениках: 114 832 обращения к индексам и
130 мс против 18 и 15 мс у пакетного `_summary_rows`.

Теперь одни и те же числа считаются в двух местах, и разъехаться они не должны:
«Реестр» и его сводка показывали бы разное про одних и тех же учеников.
Данные строим сами — в eduson_test активных учеников нет, и сверка «как есть»
проходила бы вхолостую.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.utils import timezone

from apps.dashboard import registry_service as svc
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import PENDING

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def graph():
    """
    Два направления/группы и два ученика:
      A — оплата, посещения (60 и 45 мин), членства в ОБЕИХ группах, плановое
          занятие в каждой → проверяет Σ по членствам, MAX даты и MIN даты
          при fan-out по группам;
      B — ничего, кроме активного членства → проверяет, что отсутствующие
          агрегаты дают 0/None, а не выпадение ученика из популяции.
    """
    ids: dict[str, list[int]] = {'dirs': [], 'teachers': [], 'groups': [], 'students': []}
    with connection.cursor() as cur:
        for name, total in (('__sb_dir1__', 16), ('__sb_dir2__', 8)):
            cur.execute('INSERT INTO directions (name, total_lessons, active) '
                        'VALUES (%s, %s, true) RETURNING id', [name, total])
            ids['dirs'].append(cur.fetchone()[0])

        cur.execute("INSERT INTO teachers (name) VALUES ('__sb_teacher__') RETURNING id")
        ids['teachers'].append(cur.fetchone()[0])
        teacher_id = ids['teachers'][0]

        # Первая группа — с ручной длиной курса 12 (перекрывает 16 направления),
        # вторая — без неё (8 от направления): оба пути длины курса в одной сверке.
        for i, (direction_id, lessons_total) in enumerate(zip(ids['dirs'], (12, None))):
            cur.execute(
                'INSERT INTO groups (name, direction_id, teacher_id, is_individual, '
                'lesson_duration_minutes, active, lesson_number_offset, lessons_total) '
                'VALUES (%s, %s, %s, false, 60, true, 0, %s) RETURNING id',
                [f'__sb_group{i}__', direction_id, teacher_id, lessons_total],
            )
            ids['groups'].append(cur.fetchone()[0])

        for name in ('__sb_student_a__', '__sb_student_b__'):
            cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id', [name])
            ids['students'].append(cur.fetchone()[0])
        a, b = ids['students']

        # A — в обеих группах, B — только в первой.
        for group_id, student_id, done in (
            (ids['groups'][0], a, 3), (ids['groups'][1], a, 2), (ids['groups'][0], b, 0),
        ):
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s, %s, %s, true)', [group_id, student_id, done],
            )

        cur.execute(
            'INSERT INTO payments (student_id, direction_id, subscriptions_count, '
            'lessons_count, unit_price, total_amount, paid_at, created_by) '
            "VALUES (%s, %s, 1, 8, 1000, 8000, '2026-06-01', 'test')",
            [a, ids['dirs'][0]],
        )

        # Два посещения A: 60 мин (1 урок) и 45 мин (0.5). Последнее — 10 июня.
        for n, (date, duration) in enumerate(
                ((datetime.date(2026, 6, 5), 60), (datetime.date(2026, 6, 10), 45)), start=1):
            cur.execute(
                'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
                "lesson_duration_minutes, lesson_type, submitted_by_token) "
                "VALUES (%s, %s, %s, %s, %s, 'regular', 'test') RETURNING id",
                [ids['groups'][0], teacher_id, date, n, duration],
            )
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) '
                'VALUES (%s, %s, true)', [cur.fetchone()[0], a],
            )

    now = timezone.now()
    # Плановые в обеих группах: ближайшее для A — 20 июня (MIN по двум группам).
    for group_id, day in ((ids['groups'][0], 25), (ids['groups'][1], 20)):
        PlannedLesson.objects.create(
            # seq и lesson_number — только парой (CHECK seq_number_together).
            group_id=group_id, teacher_id=teacher_id, seq=1, lesson_number=1,
            scheduled_date=datetime.date(2026, 6, day),
            scheduled_time=datetime.time(18, 0), status=PENDING,
            created_at=now, updated_at=now,
        )

    yield {'a': ids['students'][0], 'b': ids['students'][1]}

    with connection.cursor() as cur:
        groups = ids['groups']
        cur.execute('DELETE FROM planned_lessons WHERE group_id = ANY(%s)', [groups])
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


def _old_rows(today):
    return {
        r['id']: r
        for r in svc.base_students_qs(today).values(
            'id', 'balance', 'attended', 'planned', 'last_lesson', 'next_lesson',
            'cancellations')
    }


def _new_rows(today):
    return {r['student_id']: r for r in svc._summary_rows(today)}


def test_batch_matches_annotated_queryset(graph):
    old, new = _old_rows(TODAY), _new_rows(TODAY)

    assert set(new) == set(old), 'разошёлся состав активной популяции'
    for sid in old:
        for field in ('balance', 'attended', 'planned', 'last_lesson', 'next_lesson',
                      'cancellations'):
            assert new[sid][field] == old[sid][field], f'ученик {sid}, поле {field}'


def test_batch_values_are_correct(graph):
    """Сходимость путей мало стоит, если оба считают неверно — закрепляем числа."""
    a = _new_rows(TODAY)[graph['a']]
    # Остаток КУРСА: оплата (8 уроков) и журнал посещений на него не влияют.
    assert a['balance'] == 20 - 5               # курс 12 + 8, пройдено 3 + 2
    assert a['attended'] == 5                   # 3 + 2 по двум членствам
    assert a['planned'] == 20                   # 12 (ручная длина) + 8 (направление)
    assert a['last_lesson'] == datetime.date(2026, 6, 10)
    assert a['next_lesson'] == datetime.date(2026, 6, 20)   # MIN по двум группам


def test_student_without_data_stays_in_population(graph):
    """Ученик без оплат, посещений и плана не выпадает и получает нули/None."""
    b = _new_rows(TODAY)[graph['b']]
    assert b['balance'] == 12                   # весь курс группы впереди
    assert b['attended'] == 0
    assert b['planned'] == 12
    assert b['cancellations'] == 0
    assert b['last_lesson'] is None
    assert b['next_lesson'] == datetime.date(2026, 6, 25)
