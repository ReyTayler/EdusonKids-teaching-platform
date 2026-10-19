"""
Тесты общей функции веса посещения (half-lesson: 45 минут = 0.5 урока).

Вес — инвариант, от которого зависят балансы, финансы и отчёты. Функция одна на
проект именно поэтому: второе определение рядом разъедется молча.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.lessons.models import LessonAttendance
from apps.lessons.weights import attended_units_case

pytestmark = pytest.mark.django_db


@pytest.fixture
def graph():
    created = {}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__w_dir__', 16, true) RETURNING id")
        created['direction_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__w_teacher__') RETURNING id")
        created['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__w_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [created['direction_id'], created['teacher_id']])
        created['group_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__w_student__') RETURNING id")
        created['student_id'] = cur.fetchone()[0]
    created['lessons'] = []
    yield created
    with connection.cursor() as cur:
        for lid in created['lessons']:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        cur.execute('DELETE FROM students WHERE id = %s', [created['student_id']])
        cur.execute('DELETE FROM groups WHERE id = %s', [created['group_id']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [created['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id = %s', [created['direction_id']])


def _add(graph, duration, number):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-02-10', %s, %s, 'regular', 't') RETURNING id",
            [graph['group_id'], graph['teacher_id'], number, duration])
        lid = cur.fetchone()[0]
        graph['lessons'].append(lid)
        cur.execute('INSERT INTO lesson_attendance (lesson_id, student_id, present) '
                    'VALUES (%s, %s, true)', [lid, graph['student_id']])
    return lid


def test_45_minutes_weighs_half(graph):
    lid = _add(graph, 45, 1)
    row = (LessonAttendance.objects.filter(lesson_id=lid)
           .annotate(units=attended_units_case()).values('units').first())
    assert row['units'] == Decimal('0.5')


def test_other_durations_weigh_one(graph):
    for duration, number in ((60, 1), (90, 2), (120, 3)):
        lid = _add(graph, duration, number)
        row = (LessonAttendance.objects.filter(lesson_id=lid)
               .annotate(units=attended_units_case()).values('units').first())
        assert row['units'] == Decimal('1'), duration


def test_finances_helper_delegates_instead_of_redefining():
    """
    У финансов не должно остаться СВОЕГО определения веса.

    Сравнения значений тут мало: две независимые, но одинаковые копии выражения
    дали бы одинаковые числа и тест прошёл бы — а разъедутся они позже, при
    правке одной из них. Поэтому смотрим на исходник: в теле хелпера не должно
    быть конструирования Case/When, только вызов общей функции.
    """
    import inspect

    from apps.finances.repository import _attended_units_case

    source = inspect.getsource(_attended_units_case)
    assert 'attended_units_case()' in source, 'финансы обязаны звать общую функцию'
    assert 'When(' not in source, 'в финансах осталось собственное определение веса'
    assert 'Case(' not in source, 'в финансах осталось собственное определение веса'


# ── Python-вариант того же канона (импорт Eduson считает вес до записи в БД) ──

def test_lesson_weight_python_variant_matches_canon():
    from apps.lessons.weights import HALF_LESSON_MINUTES, lesson_weight

    assert HALF_LESSON_MINUTES == 45
    assert lesson_weight(45) == Decimal('0.5') and isinstance(lesson_weight(45), Decimal)
    for duration in (60, 90, 120):
        assert lesson_weight(duration) == Decimal('1'), duration


def test_sql_and_python_variants_share_one_constant():
    """Порог 45 минут — одна константа на оба варианта: литерал в Case разъехался
    бы с lesson_weight() при правке только одного из них."""
    import inspect

    from apps.lessons import weights

    source = inspect.getsource(weights.attended_units_case)
    assert 'HALF_LESSON_MINUTES' in source and '=45' not in source.replace(' ', '')
