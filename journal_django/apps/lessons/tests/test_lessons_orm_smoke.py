"""
Smoke-тесты слоя lessons (раздел 09) — сеют минимальные данные и проверяют
критичные инварианты write-путей, которые в пустой тестовой БД иначе
пропускаются (no data):
  • half-lesson: duration 45 → шаг 0.5; иначе 1;
  • lessons_done корректируется в той же транзакции (create/delete/toggle);
  • get_lesson_full / list_lessons собирают joined-поля и attendance;
  • payroll всегда считается сервером (services.create_lesson_full → record_lesson).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db.models.functions import Now

from apps.directions.models import Direction
from apps.groups.models import Group
from apps.lessons import repository, services
from apps.memberships.models import GroupMembership
from apps.payments.models import Payment
from apps.students.models import Student
from apps.teachers.models import Teacher


def _seed(duration: int = 90):
    d = Direction.objects.create(name=f'ORM-DIR-{duration}')
    t = Teacher.objects.create(name=f'ORM-T-{duration}', created_at=Now())
    g = Group.objects.create(
        name=f'ORM-G-{duration}', direction_id=d.id, teacher_id=t.id,
        is_individual=False, lesson_duration_minutes=duration, lessons_per_week=1,
        created_at=Now(),
    )
    s1 = Student.objects.create(full_name=f'ORM-S1-{duration}', created_at=Now())
    s2 = Student.objects.create(full_name=f'ORM-S2-{duration}', created_at=Now())
    GroupMembership.objects.create(group_id=g.id, student_id=s1.id, lessons_done=0)
    GroupMembership.objects.create(group_id=g.id, student_id=s2.id, lessons_done=0)
    return d, t, g, s1, s2


def _seed_payment(student_id: int, direction_id: int) -> None:
    """Оплата на 8 уроков — иначе present:true блокируется (assert_students_paid).

    created_at — NOT NULL без DB/model default (см. apps.payments.models.Payment),
    задаём явно, как в apps.lessons.tests.test_renewals_stage_sync._make_payment.
    """
    Payment.objects.create(
        student_id=student_id, direction_id=direction_id, subscriptions_count=2,
        lessons_count=8, unit_price=1000, total_amount=8000,
        paid_at='2026-01-01', created_by='test', created_at='2026-01-01T00:00:00Z',
    )


def _lessons_done(group_id, student_id) -> Decimal:
    return GroupMembership.objects.get(group_id=group_id, student_id=student_id).lessons_done


@pytest.mark.django_db
def test_create_lesson_full_increments_present_full_lesson():
    d, t, g, s1, s2 = _seed(duration=90)
    _seed_payment(s1.id, d.id)
    result = services.create_lesson_full({
        'lesson_date': '2026-01-10', 'teacher_id': t.id, 'group_id': g.id,
        'lesson_number': 1, 'lesson_duration_minutes': 90, 'lesson_type': 'regular',
        'attendance': [
            {'student_id': s1.id, 'present': True},
            {'student_id': s2.id, 'present': False},
        ],
    })
    lid = result['lesson_id']
    assert isinstance(lid, int)
    # present → +1, absent → без изменений
    assert _lessons_done(g.id, s1.id) == Decimal('1.0')
    assert _lessons_done(g.id, s2.id) == Decimal('0.0')

    full = repository.get_lesson_full(lid)
    assert full['group_name'] == g.name
    assert full['teacher_name'] == t.name
    assert len(full['attendance']) == 2
    assert full['payroll']['present_count'] == 1
    # total_students=2, present=1 → малая группа, не все пришли → smallPartial = 300
    assert full['payroll']['payment'] == Decimal('300.00')


@pytest.mark.django_db
def test_create_lesson_full_half_lesson_step():
    d, t, g, s1, s2 = _seed(duration=45)
    _seed_payment(s1.id, d.id)
    services.create_lesson_full({
        'lesson_date': '2026-01-11', 'teacher_id': t.id, 'group_id': g.id,
        'lesson_number': 1, 'lesson_duration_minutes': 45, 'lesson_type': 'regular',
        'attendance': [{'student_id': s1.id, 'present': True}],
    })
    # half-lesson: present → +0.5
    assert _lessons_done(g.id, s1.id) == Decimal('0.5')


@pytest.mark.django_db
def test_delete_lesson_full_rolls_back_lessons_done():
    d, t, g, s1, s2 = _seed(duration=90)
    _seed_payment(s1.id, d.id)
    result = services.create_lesson_full({
        'lesson_date': '2026-01-12', 'teacher_id': t.id, 'group_id': g.id,
        'lesson_number': 1, 'lesson_duration_minutes': 90,
        'attendance': [{'student_id': s1.id, 'present': True}],
    })
    lid = result['lesson_id']
    assert _lessons_done(g.id, s1.id) == Decimal('1.0')
    assert repository.delete_lesson_full(lid) is True
    assert _lessons_done(g.id, s1.id) == Decimal('0.0')
    assert repository.get_lesson_full(lid) is None


@pytest.mark.django_db
def test_update_attendance_cell_toggles_delta():
    d, t, g, s1, s2 = _seed(duration=90)
    _seed_payment(s1.id, d.id)
    result = services.create_lesson_full({
        'lesson_date': '2026-01-13', 'teacher_id': t.id, 'group_id': g.id,
        'lesson_number': 1, 'lesson_duration_minutes': 90,
        'attendance': [{'student_id': s1.id, 'present': False}],
    })
    lid = result['lesson_id']
    assert _lessons_done(g.id, s1.id) == Decimal('0.0')
    # false → true: +1
    assert repository.update_attendance_cell(lid, s1.id, True) is True
    assert _lessons_done(g.id, s1.id) == Decimal('1.0')
    # true → false: -1
    assert repository.update_attendance_cell(lid, s1.id, False) is True
    assert _lessons_done(g.id, s1.id) == Decimal('0.0')
    # GREATEST(...,0): повторный false не уводит в минус
    assert repository.update_attendance_cell(lid, s1.id, False) is True
    assert _lessons_done(g.id, s1.id) == Decimal('0.0')


@pytest.mark.django_db
def test_update_attendance_cell_missing_lesson_returns_false():
    assert repository.update_attendance_cell(999999, 1, True) is False
