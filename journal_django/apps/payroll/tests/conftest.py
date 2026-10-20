"""
conftest для тестов payroll.

Фаза 4: HMAC _make_cookie / session_cookie удалены. Аутентификация — JWT через
корневые фикстуры (admin_client, manager_client).
managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from apps.payroll.recalc import recalculate


@pytest.fixture(scope='session')
def django_db_setup():
    pass


def _get_teacher_id() -> int:
    with connection.cursor() as cur:
        cur.execute('SELECT id FROM teachers LIMIT 1')
        row = cur.fetchone()
    if not row:
        pytest.skip('No teachers in DB')
    return row[0]


@pytest.fixture
def teacher_id_fixture():
    """Выделенный тестовый учитель — изоляция агрегатов summary от продовых данных."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name) VALUES ('__pr_teacher__') RETURNING id"
        )
        tid = cur.fetchone()[0]
    yield tid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [tid])


@pytest.fixture
def direction_fixture():
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__pr_dir__', 16, true) RETURNING id"
        )
        did = cur.fetchone()[0]
    yield did
    with connection.cursor() as cur:
        cur.execute('DELETE FROM directions WHERE id = %s', [did])


@pytest.fixture
def group_fixture(direction_fixture, teacher_id_fixture):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) VALUES ('__pr_group__', %s, %s, false, 60, true, 0) "
            "RETURNING id",
            [direction_fixture, teacher_id_fixture],
        )
        gid = cur.fetchone()[0]
    yield gid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.fixture
def student_fixture():
    """Ученик для строк lesson_attendance (исключения из headcount зарплаты)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name) VALUES ('__pr_student__') RETURNING id"
        )
        student_id = cur.fetchone()[0]
    yield student_id
    with connection.cursor() as cur:
        # Свои строки посещаемости убираем сами: порядок разрушения фикстур
        # не гарантирует, что урок уже удалён, а FK этого не простит.
        cur.execute('DELETE FROM lesson_attendance WHERE student_id = %s', [student_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.fixture
def payroll_fixture(group_fixture, teacher_id_fixture):
    """Один урок + строка payroll. Возвращает (payroll_id, lesson_id)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-04-10', 1, 60, 'regular', 'test') RETURNING id",
            [group_fixture, teacher_id_fixture],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, payment, penalty) "
            "VALUES (%s, %s, 5, 4, 650.00, 0) RETURNING id",
            [lesson_id, teacher_id_fixture],
        )
        payroll_id = cur.fetchone()[0]
    yield payroll_id, lesson_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE id = %s', [payroll_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


# ---------------------------------------------------------------------------
# Мини-школа для пересчёта начислений (test_recalc, test_recalculate_command).
# Помощники импортируются тестами напрямую: from apps.payroll.tests.conftest import …
# ---------------------------------------------------------------------------

UTC = datetime.timezone.utc
D = Decimal


@pytest.fixture
def db_school(db):
    """Маленькая выдуманная школа: направление, два преподавателя, группа, ученики.
    Версии ставок школы из общей БД убираем внутри транзакции теста — иначе
    чужая версия поменяла бы выбор «действующей на дату»."""
    from apps.directions.models import Direction
    from apps.groups.models import Group
    from apps.payroll.models import PayRateVersion
    from apps.students.models import Student
    from apps.teachers.models import Teacher

    PayRateVersion.objects.all().delete()
    now = timezone.now()
    direction = Direction.objects.create(name='Тест Движок Направление', total_lessons=36)
    t1 = Teacher.objects.create(name='Тест Движок Преподаватель 1', created_at=now)
    t2 = Teacher.objects.create(name='Тест Движок Преподаватель 2', created_at=now)
    group = Group.objects.create(
        name='Тест Движок Группа', direction=direction, teacher=t1, is_individual=False,
        lesson_duration_minutes=90, active=True, created_at=now)
    students = [Student.objects.create(full_name=f'Тест Движок Ученик {i}', created_at=now)
                for i in range(4)]
    return {'direction': direction, 't1': t1, 't2': t2, 'group': group, 'students': students}


def db_version(effective_from, direction=None, dir_group=None, **overrides):
    from apps.payroll.models import PayRateDirection, PayRateVersion
    fields = {
        'effective_from': effective_from, 'group_rate': D('600'), 'individual_rate': D('800'),
        'cancel_mode': 'percent', 'cancel_value': D('50'),
    }
    fields.update(overrides)
    v = PayRateVersion.objects.create(**fields)
    if direction is not None:
        PayRateDirection.objects.create(version=v, direction=direction, group_rate=dir_group)
    return v


def db_lesson(school, day, number, *, teacher=None, token='acct:1', submitted_at=None,
              present=(True, True, False), unpaid_skip=(), lesson_type='regular'):
    from apps.lessons.models import Lesson, LessonAttendance
    lesson = Lesson.objects.create(
        group=school['group'], teacher=teacher or school['t1'], lesson_date=day,
        lesson_number=D(str(number)), lesson_duration_minutes=90, lesson_type=lesson_type,
        submitted_at=submitted_at or datetime.datetime.combine(
            day, datetime.time(9, 0), tzinfo=UTC),
        submitted_by_token=token)
    for student, flag in zip(school['students'], present):
        LessonAttendance.objects.create(lesson=lesson, student=student, present=flag)
    for student in unpaid_skip:
        LessonAttendance.objects.create(lesson=lesson, student=student, present=False,
                                        unpaid_skip=True)
    return lesson


_DEFAULT = object()


def db_marker(school, day, time=datetime.time(15, 0), teacher=_DEFAULT, substitute=None):
    """Маркер отмены (status='cancelled', seq NULL). teacher=None — без преподавателя."""
    from apps.scheduling.models import PlannedLesson
    now = timezone.now()
    return PlannedLesson.objects.create(
        group=school['group'], seq=None, lesson_number=None, scheduled_date=day,
        scheduled_time=time, teacher=school['t1'] if teacher is _DEFAULT else teacher,
        substitute_teacher=substitute, status='cancelled', created_at=now, updated_at=now)


def db_orphan(school, day, time=datetime.time(15, 0), payment='300'):
    """Начисление отмены, чей маркер удалён (planned_lesson NULL)."""
    from apps.payroll.models import CancellationPay
    return CancellationPay.objects.create(
        teacher=school['t1'], group=school['group'], lesson_date=day, scheduled_time=time,
        payment=D(payment), calc_payment=D(payment))


def recalc_group(school, **kwargs):
    return recalculate(group_ids=[school['group'].id], **kwargs)
