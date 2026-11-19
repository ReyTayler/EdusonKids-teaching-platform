import datetime as dt

import pytest

from apps.availability.models import AvailabilityRule

pytestmark = pytest.mark.django_db
WIN = '?from=2030-01-07&to=2030-01-13'


def test_teacher_calendar_has_availability(own_teacher_client, teacher):
    assert own_teacher_client.get('/api/calendar' + WIN).json()['availability'] is None
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                    start_minute=10 * 60, end_minute=14 * 60)
    body = own_teacher_client.get('/api/calendar' + WIN).json()
    assert body['availability']['2030-01-07'] == [['10:00', '14:00']]
    assert body['availability']['2030-01-08'] == []


def test_admin_calendar_availability_only_for_one_teacher(admin_client, teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                    start_minute=10 * 60, end_minute=14 * 60)
    assert admin_client.get('/api/admin/calendar' + WIN).json()['availability'] is None
    body = admin_client.get(f'/api/admin/calendar{WIN}&teacher_id={teacher.id}').json()
    assert body['availability']['2030-01-07'] == [['10:00', '14:00']]


def test_admin_calendar_teacher_without_schedule(admin_client, teacher):
    body = admin_client.get(f'/api/admin/calendar{WIN}&teacher_id={teacher.id}').json()
    assert body['availability'] is None


def test_admin_calendar_availability_by_teacher(admin_client, teacher, other_teacher):
    """Без teacher_id — окна всех активных преподавателей с графиком (ключ — id
    строкой); преподавателя без графика в словаре нет."""
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                    start_minute=10 * 60, end_minute=14 * 60)
    by_teacher = admin_client.get('/api/admin/calendar' + WIN).json()['availabilityByTeacher']
    assert by_teacher[str(teacher.id)]['2030-01-07'] == [['10:00', '14:00']]
    assert by_teacher[str(teacher.id)]['2030-01-08'] == []
    assert str(other_teacher.id) not in by_teacher


def test_admin_calendar_availability_by_teacher_skips_archived_and_service(admin_client, teacher, other_teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                    start_minute=10 * 60, end_minute=14 * 60)
    AvailabilityRule.objects.create(teacher=other_teacher, day_of_week=1,
                                    start_minute=10 * 60, end_minute=14 * 60)
    teacher.active = False
    teacher.save(update_fields=['active'])
    other_teacher.is_service = True
    other_teacher.save(update_fields=['is_service'])
    by_teacher = admin_client.get('/api/admin/calendar' + WIN).json()['availabilityByTeacher']
    assert str(teacher.id) not in by_teacher
    assert str(other_teacher.id) not in by_teacher


def test_admin_calendar_availability_by_teacher_null_for_one_teacher(admin_client, teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                    start_minute=10 * 60, end_minute=14 * 60)
    body = admin_client.get(f'/api/admin/calendar{WIN}&teacher_id={teacher.id}').json()
    assert body['availabilityByTeacher'] is None


def test_teacher_calendar_has_no_availability_by_teacher(own_teacher_client, teacher):
    assert 'availabilityByTeacher' not in own_teacher_client.get('/api/calendar' + WIN).json()
