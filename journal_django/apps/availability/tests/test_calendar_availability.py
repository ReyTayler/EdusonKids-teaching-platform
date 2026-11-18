import datetime as dt

import pytest

from apps.availability.models import AvailabilityRule

pytestmark = pytest.mark.django_db
WIN = '?from=2030-01-07&to=2030-01-13'


def test_teacher_calendar_has_availability(own_teacher_client, teacher):
    assert own_teacher_client.get('/api/calendar' + WIN).json()['availability'] is None
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                    start_time=dt.time(10), end_time=dt.time(14))
    body = own_teacher_client.get('/api/calendar' + WIN).json()
    assert body['availability']['2030-01-07'] == [['10:00', '14:00']]
    assert body['availability']['2030-01-08'] == []


def test_admin_calendar_availability_only_for_one_teacher(admin_client, teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                    start_time=dt.time(10), end_time=dt.time(14))
    assert admin_client.get('/api/admin/calendar' + WIN).json()['availability'] is None
    body = admin_client.get(f'/api/admin/calendar{WIN}&teacher_id={teacher.id}').json()
    assert body['availability']['2030-01-07'] == [['10:00', '14:00']]


def test_admin_calendar_teacher_without_schedule(admin_client, teacher):
    body = admin_client.get(f'/api/admin/calendar{WIN}&teacher_id={teacher.id}').json()
    assert body['availability'] is None
