"""
Минимальная выдуманная школа для тестов (форк Eduson).

Часть тестов групп, членств, оплат и учеников брала «любую» строку из общей
тестовой БД и без неё делала pytest.skip. В KOTOKOD такие строки лежали в
journal_test с разработки; eduson_test строится миграциями пустой, и тесты молча
не выполнялись. Теперь они берут данные отсюда — детерминированно, не завися от
того, что оставили другие тесты.

Почему не сид в самой БД: тесты с django_db(transaction=True) после себя делают
flush всех таблиц — сид исчез бы раньше, чем до него дойдут, а KOTOKOD прямо
требует не сеять тестовую БД (teacher_spa/tests/test_concurrent_submit.py).
Поэтому генератор зовут изнутри теста: строки живут в его транзакции и
откатываются вместе с ней.

Значения явно фейковые — никаких реальных имён, телефонов и адресов.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.utils import timezone

_TOKEN = '__minimal_school__'


def ensure_minimal_school() -> dict[str, int]:
    """
    Гарантировать минимум данных школы и вернуть их id.

    Идемпотентно (get_or_create по фиксированным ключам): повторный вызов в том
    же тесте вернёт те же id и ничего не задвоит.

    Ключи результата:
      direction_id, teacher_id, group_id (активная, со слотом), lesson_id;
      student_id — ученик группы: активное членство, посещение урока, оплата;
      free_student_id — ученик БЕЗ членства и оплат: для тестов, которые сами
        создают пару «группа + ученик» (POST/upsert ждут 201, а не конфликт
        уникальности с готовым членством).
    """
    from apps.directions.models import Direction
    from apps.groups.models import Group, GroupScheduleSlot
    from apps.lessons.models import Lesson, LessonAttendance
    from apps.memberships.models import GroupMembership
    from apps.payments.models import Payment
    from apps.students.models import Student
    from apps.teachers.models import Teacher

    now = timezone.now()
    direction, _ = Direction.objects.get_or_create(
        name='Тест Направление 1', defaults={'total_lessons': 36, 'color': '#4F59F9'},
    )
    teacher, _ = Teacher.objects.get_or_create(
        name='Тест Преподаватель 1',
        defaults={'email': 'teacher1@example.com', 'phone': '70000000001',
                  'created_at': now},
    )
    group, _ = Group.objects.get_or_create(
        name='Тест Группа 1',
        defaults={'direction': direction, 'teacher': teacher, 'is_individual': False,
                  'lesson_duration_minutes': 90, 'lessons_per_week': 1,
                  'active': True, 'created_at': now},
    )
    GroupScheduleSlot.objects.get_or_create(
        group=group, day_of_week=1, start_time=datetime.time(10, 0),
    )
    student, _ = Student.objects.get_or_create(
        full_name='Тест Ученик 1',
        defaults={'parent1_name': 'Тест Родитель 1', 'parent1_phone': '70000000002',
                  'parent1_email': 'parent1@example.com', 'created_at': now},
    )
    free_student, _ = Student.objects.get_or_create(
        full_name='Тест Ученик 2',
        defaults={'parent1_name': 'Тест Родитель 2', 'parent1_phone': '70000000003',
                  'parent1_email': 'parent2@example.com', 'created_at': now},
    )
    # Одно проведённое 90-минутное занятие = 1 урок (вес 45 мин был бы 0.5),
    # поэтому у членства lessons_done = 1 — согласовано с посещением ниже.
    GroupMembership.objects.get_or_create(
        group=group, student=student,
        defaults={'lessons_done': Decimal('1'), 'active': True,
                  'start_date': datetime.date(2026, 1, 12)},
    )
    lesson, _ = Lesson.objects.get_or_create(
        group=group, lesson_date=datetime.date(2026, 1, 12), lesson_number=Decimal('1'),
        submitted_by_token=_TOKEN,
        defaults={'teacher': teacher, 'lesson_duration_minutes': 90,
                  'lesson_type': 'regular', 'submitted_at': now},
    )
    if not LessonAttendance.objects.filter(lesson=lesson, student=student).exists():
        LessonAttendance.objects.create(lesson=lesson, student=student, present=True)
    # Инварианты оплат: total_amount = unit_price × subscriptions_count,
    # у purchase lessons_count > 0 (абонемент = 4 урока).
    unit_price = Decimal('4000.00')
    subscriptions = 1
    Payment.objects.get_or_create(
        student=student, direction=direction, created_by=_TOKEN,
        defaults={'kind': 'purchase', 'subscriptions_count': subscriptions,
                  'lessons_count': Decimal(4 * subscriptions), 'unit_price': unit_price,
                  'total_amount': unit_price * subscriptions,
                  'paid_at': datetime.date(2026, 1, 5), 'created_at': now},
    )
    return {
        'direction_id': direction.id, 'teacher_id': teacher.id, 'group_id': group.id,
        'lesson_id': lesson.id, 'student_id': student.id,
        'free_student_id': free_student.id,
    }
