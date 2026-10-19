from __future__ import annotations

import pytest
from django.apps import apps

from apps.changelog import labels, registry

pytestmark = pytest.mark.django_db


def test_registry_covers_all_tracked_models():
    """Каждая модель с event-моделью есть в registry, и наоборот."""
    tracked_in_db = set()
    for model in apps.get_models():
        name = model.__name__
        if name.endswith('Event') and model._meta.app_label != 'pghistory':
            tracked_in_db.add(f"{model._meta.app_label}.{name[:-5]}")
    assert tracked_in_db == set(registry.TRACKED.keys())


def test_event_model_lookup():
    ev = registry.event_model('groups.Group')
    assert ev is apps.get_model('groups', 'GroupEvent')


def test_account_not_revertable():
    assert registry.TRACKED['accounts.Account'].revertable is False
    assert registry.TRACKED['groups.Group'].revertable is True


def test_attendance_identity_is_composite():
    """У lesson_attendance реальный PK составной — identity из двух полей."""
    assert registry.TRACKED['lessons.LessonAttendance'].identity == ('lesson_id', 'student_id')
    assert registry.TRACKED['groups.Group'].identity == ('id',)


def test_operation_from_url():
    assert labels.resolve_operation('POST', '/api/admin/groups') == 'group.create'
    assert labels.resolve_operation('PATCH', '/api/admin/groups/5') == 'group.update'
    assert labels.resolve_operation('POST', '/api/admin/groups/5/plan/12/reschedule') == 'plan.reschedule'
    assert labels.resolve_operation('POST', '/api/submitLesson') == 'lesson.submit'
    assert labels.resolve_operation('DELETE', '/api/admin/payments/9') == 'payment.delete'
    assert labels.resolve_operation('POST', '/api/admin/renewals/7/unfreeze') == 'renewal.unfreeze'
    assert labels.resolve_operation('PATCH', '/api/admin/students/7/manager') == 'student.manager_update'
    assert labels.resolve_operation('GET', '/api/admin/groups') == 'other'


def test_operation_from_url_keeps_removed_endpoints():
    """Правила снятых эндпоинтов остаются: операция резолвится при ЧТЕНИИ ленты из
    сохранённых method+url, поэтому исторические события обязаны узнаваться и после
    удаления самого эндпоинта. Без этого они схлопываются в 'other' и выпадают из
    фильтра — тот же приём, что для membership.place и extra_lesson.waive."""
    assert labels.resolve_operation('POST', '/api/admin/students/7/status') == 'student.status'
    assert labels.resolve_operation('POST', '/api/admin/students/7/resume') == 'student.resume'
    assert labels.resolve_operation('DELETE', '/api/admin/students/7') == 'student.delete'


def test_rule_for_operation_roundtrip():
    method, pattern = labels.rule_for_operation('lesson.submit')
    assert method == 'POST'
    assert pattern.match('/api/submitLesson')
    assert labels.rule_for_operation('no.such.op') is None
