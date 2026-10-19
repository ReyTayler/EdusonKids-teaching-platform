"""
Smoke-тесты ORM-переноса FinancesRepository (раздел 09).

Сеют минимальный финансовый сценарий и проверяют точность баланса/FIFO-входов,
которые в пустой тестовой БД иначе пропускаются.

Сценарий: 1 абонемент (subscriptions_count=1 → 4 урока), 1 посещённый 90-мин урок
(1 единица). Баланс = 4 − 1 = 3.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db.models.functions import Now

from apps.directions.models import Direction
from apps.finances import repository
from apps.groups.models import Group
from apps.lessons.models import Lesson, LessonAttendance
from apps.payments.models import Payment
from apps.students.models import Student
from apps.teachers.models import Teacher


def _seed():
    d = Direction.objects.create(
        name='FIN-DIR', total_lessons=40,
        color='#abcdef',
    )
    t = Teacher.objects.create(name='FIN-T', created_at=Now())
    g = Group.objects.create(
        name='FIN-G', direction_id=d.id, teacher_id=t.id, is_individual=False,
        lesson_duration_minutes=90, lessons_per_week=1, created_at=Now(),
    )
    s = Student.objects.create(full_name='FIN-S', created_at=Now())
    # Оплата: 1 абонемент = 4 урока, цена 4000 (1000/урок)
    Payment.objects.create(
        student_id=s.id, direction_id=d.id, subscriptions_count=1, lessons_count=1 * 4,
        unit_price=Decimal('4000.00'), total_amount=Decimal('4000.00'),
        paid_at='2026-01-01', created_at=Now(),
    )
    # Один посещённый 90-мин урок → 1 единица
    lesson = Lesson.objects.create(
        group_id=g.id, teacher_id=t.id, lesson_date='2026-01-05',
        lesson_number=1, lesson_duration_minutes=90, lesson_type='regular',
        submitted_by_token='x', submitted_at=Now(),
    )
    LessonAttendance.objects.create(lesson_id=lesson.id, student_id=s.id, present=True)
    return d, t, g, s


@pytest.mark.django_db
def test_balance_for_student():
    d, t, g, s = _seed()
    # purchased 4 − attended 1 = 3
    assert repository.balance_for_student(s.id) == 3


@pytest.mark.django_db
def test_total_paid_amount():
    d, t, g, s = _seed()
    assert repository.total_paid_amount(s.id) == 4000


@pytest.mark.django_db
def test_paid_by_direction_rows():
    d, t, g, s = _seed()
    rows = repository.paid_by_direction_rows(s.id)
    assert len(rows) == 1
    r = rows[0]
    assert r['direction_id'] == d.id
    assert r['direction_name'] == 'FIN-DIR'
    assert r['direction_color'] == '#abcdef'
    assert repository._js_number(r['total_paid_amount']) == 4000


@pytest.mark.django_db
def test_attended_by_direction_rows():
    d, t, g, s = _seed()
    rows = repository.attended_by_direction_rows(s.id)
    assert len(rows) == 1
    r = rows[0]
    assert r['direction_id'] == d.id
    assert r['direction_name'] == 'FIN-DIR'
    assert repository._js_number(r['attended_lessons']) == 1


@pytest.mark.django_db
def test_fifo_inputs():
    d, t, g, s = _seed()
    res = repository.fifo_inputs()
    key = str(s.id)
    assert key in res['keys']
    assert res['purchased_by_key'][key] == 4
    # цена за урок = 4000 / 4 = 1000
    assert res['lots_by_key'][key][0]['price_per_lesson'] == Decimal('1000')
    assert res['consumed_by_key'][key] == Decimal('1')
    assert res['cons_by_key'][key][0]['date'] == '2026-01-05'
    assert res['cons_by_key'][key][0]['units'] == Decimal('1')
    assert res['cons_by_key'][key][0]['direction_id'] == d.id
