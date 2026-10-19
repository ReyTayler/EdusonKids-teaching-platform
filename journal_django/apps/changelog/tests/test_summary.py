"""Человекочитаемые описания операций (apps/changelog/summary.py)."""
from __future__ import annotations

import pghistory
import pytest
from django.utils import timezone

from apps.changelog.summary import Lookups, describe_event
from apps.directions.models import Direction
from apps.groups.models import Group
from apps.memberships.models import GroupMembership
from apps.memberships.repository import transfer_membership
from apps.scheduling.models import PlannedLesson
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# describe_event — описание одного события (модалка деталей); чистая функция.
# ---------------------------------------------------------------------------

_LK = Lookups(groups={7: 'ПИ1012'}, students={3: 'Иван Тестов'},
              teachers={1: 'Пётр', 2: 'Мария'})


def test_describe_event_attendance():
    ev = {'entity': 'attendance', 'pgh_label': 'update',
          'pgh_data': {'student_id': 3, 'present': True},
          'pgh_diff': {'present': [False, True]}, 'pgh_obj_id': '10'}
    assert describe_event(ev, _LK) == 'Иван Тестов: был'


def test_describe_event_planned_lesson_reschedule():
    ev = {'entity': 'planned_lesson', 'pgh_label': 'update',
          'pgh_data': {'group_id': 7, 'lesson_number': 20,
                       'scheduled_date': '2026-07-09', 'scheduled_time': '12:00:00'},
          'pgh_diff': {'scheduled_date': ['2026-07-09', '2026-07-07']},
          'pgh_obj_id': '5'}
    assert describe_event(ev, _LK) == 'Перенос ПИ1012 №20: 2026-07-09 12:00 → 2026-07-07 12:00'


def test_describe_event_lesson_insert():
    ev = {'entity': 'lesson', 'pgh_label': 'insert',
          'pgh_data': {'group_id': 7, 'lesson_number': 3, 'lesson_date': '2026-07-01'},
          'pgh_diff': {}, 'pgh_obj_id': '8'}
    assert describe_event(ev, _LK) == 'Проведён урок ПИ1012 №3 (2026-07-01)'


def test_describe_event_generic_update_uses_russian_fields():
    ev = {'entity': 'direction', 'pgh_label': 'update',
          'pgh_data': {'name': 'Робототехника'},
          'pgh_diff': {'name': ['Робо', 'Робототехника'], 'subscription_price': [100, 200]},
          'pgh_obj_id': '2'}
    out = describe_event(ev, _LK)
    assert out == 'Направление «Робототехника»: изменено — название, цена абонемента'


def test_describe_event_never_empty_for_unknown_entity():
    ev = {'entity': None, 'pgh_label': 'insert', 'pgh_data': {}, 'pgh_diff': {},
          'pgh_obj_id': '99'}
    assert describe_event(ev, _LK).strip()


@pytest.fixture
def group():
    t = Teacher.objects.create(name='__sum_t__', created_at=timezone.now())
    d = Direction.objects.create(name='__sum_d__')
    return Group.objects.create(name='ПИ1012', direction=d, teacher=t, is_individual=False, created_at=timezone.now())


def _feed_top(client):
    return client.get('/api/admin/changelog?page_size=1').json()['rows'][0]


def test_summary_plan_reschedule(admin_client, group):
    pl = PlannedLesson.objects.create(
        group=group, seq=20, lesson_number=20, scheduled_date='2026-07-09',
        scheduled_time='12:00', status='pending',
        created_at=timezone.now(), updated_at=timezone.now(),
    )
    with pghistory.context(url='/t', method='POST', operation='plan.reschedule'):
        PlannedLesson.objects.filter(id=pl.id).update(scheduled_date='2026-07-07')
    row = _feed_top(admin_client)
    assert row['summary'] == 'Перенос ПИ1012 №20: 2026-07-09 12:00 → 2026-07-07 12:00'


def test_summary_status_change(admin_client, group):
    pl = PlannedLesson.objects.create(
        group=group, seq=18, lesson_number=18, scheduled_date='2026-07-09',
        scheduled_time='12:00', status='pending',
        created_at=timezone.now(), updated_at=timezone.now(),
    )
    with pghistory.context(url='/t', method='POST'):
        PlannedLesson.objects.filter(id=pl.id).update(status='done')
    row = _feed_top(admin_client)
    assert row['summary'] == 'Статус ПИ1012 №18: запланирован → проведён'


def test_summary_membership(admin_client, group):
    s = Student.objects.create(full_name='Иван Тестов', created_at=timezone.now())
    with pghistory.context(url='/api/admin/memberships', method='POST'):
        GroupMembership.objects.create(group=group, student=s, active=True,
                                       lessons_done=0)
    row = _feed_top(admin_client)
    assert row['summary'] == 'Зачисление: Иван Тестов → ПИ1012'


def test_summary_membership_transfer(admin_client, group):
    target_group = Group.objects.create(
        name='ПИ1013', direction=group.direction, teacher=group.teacher, is_individual=False, created_at=timezone.now(),
    )
    s = Student.objects.create(full_name='Иван Тестов', created_at=timezone.now())
    old = GroupMembership.objects.create(group=group, student=s, active=True, lessons_done=32)

    with pghistory.context(url=f'/api/admin/memberships/{old.id}/transfer', method='POST'):
        transfer_membership(old.id, target_group.id)

    row = _feed_top(admin_client)
    assert row['summary'] == 'Перевод: Иван Тестов из ПИ1012 в ПИ1013'


def test_summary_membership_transfer_reactivation(admin_client, group):
    """Целевая группа уже когда-то видела этого ученика (неактивная membership) —
    перевод реактивирует её (UPDATE active False→True), а не создаёт новую строку.
    Обе стороны перевода — 'update'-события, а не insert/update — проверяет,
    что old/new различаются по diff направления, а не по pgh_label."""
    target_group = Group.objects.create(
        name='ПИ1014', direction=group.direction, teacher=group.teacher, is_individual=False, created_at=timezone.now(),
    )
    s = Student.objects.create(full_name='Мария Тестова', created_at=timezone.now())
    GroupMembership.objects.create(group=target_group, student=s, active=False, lessons_done=4)
    old = GroupMembership.objects.create(group=group, student=s, active=True, lessons_done=32)

    with pghistory.context(url=f'/api/admin/memberships/{old.id}/transfer', method='POST'):
        transfer_membership(old.id, target_group.id)

    row = _feed_top(admin_client)
    assert row['summary'] == 'Перевод: Мария Тестова из ПИ1012 в ПИ1014'


def test_summary_generic_update(admin_client):
    d = Direction.objects.create(name='__sum_gen__')
    with pghistory.context(url='/api/admin/directions/1', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__sum_gen2__')
    row = _feed_top(admin_client)
    assert 'Направление' in row['summary']
    assert 'name' in row['summary']  # перечень изменённых полей


def test_summary_soft_delete_reads_as_archive(admin_client):
    d = Direction.objects.create(name='__sum_arch__')
    with pghistory.context(url='/api/admin/directions/1', method='DELETE'):
        Direction.objects.filter(id=d.id).update(active=False)
    row = _feed_top(admin_client)
    assert row['summary'] == 'Направление «__sum_arch__»: в архив'


def test_resolve_operation_refund():
    from apps.changelog.labels import resolve_operation
    assert resolve_operation('POST', '/api/admin/students/7/refund') == 'payment.refund'


def test_describe_event_refund_insert():
    from apps.changelog.summary import describe_event, Lookups
    ev = {
        'entity': 'payment', 'pgh_label': 'insert',
        'pgh_data': {'student_id': 1, 'kind': 'refund',
                     'total_amount': '-3000.00', 'lessons_count': '-3.0'},
        'pgh_diff': {},
    }
    out = describe_event(ev, Lookups(students={1: 'Иванов'}))
    assert out.startswith('Возврат 3000 ₽')
    assert 'Иванов' in out


def test_build_summary_refund_sums_rows_per_direction():
    """Возврат пишется строкой на каждое направление — в сводке нужна общая сумма."""
    from apps.changelog.summary import build_summary, Lookups
    events = [
        {'entity': 'payment', 'pgh_label': 'insert',
         'pgh_data': {'student_id': 1, 'kind': 'refund', 'total_amount': '-4000.00'},
         'pgh_diff': {}},
        {'entity': 'payment', 'pgh_label': 'insert',
         'pgh_data': {'student_id': 1, 'kind': 'refund', 'total_amount': '-2000.00'},
         'pgh_diff': {}},
    ]
    out = build_summary('payment.refund', events, Lookups(students={1: 'Иванов'}))
    assert out == 'Возврат 6000 ₽: Иванов'


def test_build_summary_single_payment_unchanged():
    from apps.changelog.summary import build_summary, Lookups
    events = [
        {'entity': 'payment', 'pgh_label': 'insert',
         'pgh_data': {'student_id': 1, 'kind': 'purchase', 'total_amount': '4000.00'},
         'pgh_diff': {}},
    ]
    out = build_summary('payment.create', events, Lookups(students={1: 'Иванов'}))
    assert out == 'Оплата 4000 ₽: Иванов'


def test_describe_event_non_block_insert():
    from apps.changelog.summary import describe_event, Lookups
    ev = {
        'entity': 'payment', 'pgh_label': 'insert',
        'pgh_data': {'student_id': 1, 'kind': 'purchase',
                     'total_amount': '2000.00', 'lessons_count': '2.0'},
        'pgh_diff': {},
    }
    out = describe_event(ev, Lookups(students={1: 'Иванов'}))
    assert '(2 уроков)' in out
