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
    assert describe_event(ev, _LK) == 'Перенос ПИ1012 №20: 09.07.2026 12:00 → 07.07.2026 12:00'


def test_describe_event_lesson_insert():
    ev = {'entity': 'lesson', 'pgh_label': 'insert',
          'pgh_data': {'group_id': 7, 'lesson_number': 3, 'lesson_date': '2026-07-01'},
          'pgh_diff': {}, 'pgh_obj_id': '8'}
    assert describe_event(ev, _LK) == 'Проведён урок ПИ1012 №3 (01.07.2026)'


def test_describe_event_generic_update_uses_russian_fields():
    ev = {'entity': 'direction', 'pgh_label': 'update',
          'pgh_data': {'name': 'Робототехника'},
          'pgh_diff': {'name': ['Робо', 'Робототехника'], 'subscription_price': [100, 200]},
          'pgh_obj_id': '2'}
    out = describe_event(ev, _LK)
    assert out == ('Направление «Робототехника»: название: Робо → Робототехника; '
                   'цена абонемента: 100\u202f₽ → 200\u202f₽')


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
    assert row['summary'] == 'Перенос ПИ1012 №20: 09.07.2026 12:00 → 07.07.2026 12:00'


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
    assert 'название: __sum_gen__ → __sum_gen2__' in row['summary']  # было → стало


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


# ---------------------------------------------------------------------------
# Подписи операций в духе журнала старой системы: «что сделали, с чем, было → стало».
# ---------------------------------------------------------------------------

from apps.changelog.summary import build_summary  # noqa: E402

_N = ' '
_RLK = Lookups(groups={7: 'ПИ1012'}, teachers={1: 'Пётр', 2: 'Мария'},
               directions={3: 'Вайб-кодинг'}, students={3: 'Иван Тестов'},
               lessons={50: 'ПИ1012 №5 (09.07.2026)'},
               renewal_stages={1: 'Урок 4', 2: 'Думает'})


def _ev(entity, label, data, diff=None, obj_id=1):
    return {'entity': entity, 'pgh_label': label, 'pgh_data': data,
            'pgh_diff': diff, 'pgh_obj_id': obj_id}


def _payrolls(n):
    return [_ev('payroll', 'update', {'teacher_id': 1, 'lesson_id': i, 'payment': 600},
                {'payment': [500, 600]}, obj_id=i) for i in range(n)]


def test_build_summary_rate_version_create():
    events = [
        _ev('pay_rate_version', 'insert', {
            'effective_from': '2026-06-01', 'group_rate': 1000.0, 'individual_rate': 600.0,
            'cancel_mode': 'percent', 'cancel_value': 50.0, 'penalty_enabled': False}),
        _ev('pay_rate_direction', 'insert', {'direction_id': 3, 'group_rate': None,
                                             'individual_rate': 800.0}),
        *_payrolls(3),
    ]
    assert build_summary('payroll.rate_version_create', events, _RLK) == (
        'Новые ставки школы с 01.06.2026; начисления пересчитаны')


def test_build_summary_rate_version_update():
    events = [
        _ev('pay_rate_version', 'update', {'effective_from': '2026-06-01'},
            {'group_rate': [1000.0, 600.0]}),
        *_payrolls(2),
    ]
    assert build_summary('payroll.rate_version_update', events, _RLK) == (
        'Изменены ставки школы с 01.06.2026; начисления пересчитаны')


def test_build_summary_personal_rate_create_with_term():
    events = [
        _ev('personal_rate', 'insert', {'teacher_id': 1, 'group_id': 7, 'is_active': True}),
        _ev('personal_rate_term', 'insert', {
            'teacher_id': 1, 'group_id': 7, 'kind': 'plus_fixed', 'value': 150.0,
            'lesson_from': 5.0, 'lesson_to': 10.0, 'valid_from': None}),
        *_payrolls(4),
    ]
    assert build_summary('payroll.personal_rate_create', events, _RLK) == (
        'Новая персональная ставка Пётр — ПИ1012; начисления пересчитаны')


def test_build_summary_personal_rate_update_is_formal():
    term = {'teacher_id': 1, 'group_id': 7, 'lesson_from': None, 'lesson_to': None}
    events = [
        _ev('personal_rate_term', 'update', {**term, 'kind': 'fixed', 'value': 700.0},
            {'valid_until': [None, '2026-10-23']}, obj_id=3),
        _ev('personal_rate_term', 'insert',
            {**term, 'kind': 'plus_percent', 'value': 10.0, 'valid_from': '2026-10-23'}, obj_id=4),
    ]
    assert build_summary('payroll.personal_rate_update', events, _RLK) == (
        'Изменена персональная ставка Пётр — ПИ1012')


def test_build_summary_personal_rate_deactivate():
    events = [
        _ev('personal_rate', 'update', {'teacher_id': 1, 'group_id': 7},
            {'is_active': [True, False]}),
        _ev('personal_rate_term', 'update', {
            'teacher_id': 1, 'group_id': 7, 'kind': 'fixed', 'value': 700.0,
            'lesson_from': None, 'lesson_to': None}, {'valid_until': [None, '2026-10-23']}),
    ]
    assert build_summary('payroll.personal_rate_deactivate', events, _RLK) == (
        'Выключена персональная ставка Пётр — ПИ1012')


def test_build_summary_manual_payroll_edit_hides_amount():
    events = [_ev('payroll', 'update',
                  {'teacher_id': 1, 'lesson_id': 50, 'payment': 800.0, 'is_manual': True},
                  {'payment': [600.0, 800.0], 'is_manual': [False, True]})]
    assert build_summary('payroll.update', events, _RLK) == (
        'Начисление изменено вручную: Пётр за ПИ1012 №5 (09.07.2026)')


def test_build_summary_group_update_shows_teacher_names():
    events = [_ev('group', 'update', {'name': 'ПИ1012', 'teacher_id': 2},
                  {'teacher_id': [1, 2]}, obj_id=7)]
    assert build_summary('group.update', events, _RLK) == (
        'Изменена группа «ПИ1012»: преподаватель: Пётр → Мария')


def test_build_summary_eduson_sync_lists_group_changes():
    events = [
        _ev('group', 'update', {'name': 'ПИ1012', 'teacher_id': 2},
            {'teacher_id': [1, 2]}, obj_id=7),
        _ev('schedule_slot', 'delete', {'group_id': 7, 'day_of_week': 4, 'start_time': '17:00:00'}),
        _ev('schedule_slot', 'insert', {'group_id': 7, 'day_of_week': 1, 'start_time': '13:00:00'}),
    ]
    assert build_summary('eduson.sync', events, _RLK) == (
        'Обновление данных с прода: ПИ1012 — преподаватель: Пётр → Мария, '
        'расписание Чт 17:00 → Пн 13:00')


def test_build_summary_eduson_import_counts():
    events = [_ev('group', 'insert', {'name': f'Г{i}'}, obj_id=i) for i in range(2)]
    events += [_ev('lesson', 'insert', {'group_id': 7}, obj_id=i) for i in range(3)]
    assert build_summary('eduson.import', events, _RLK) == (
        'Перенос данных из старой системы: групп — 2, уроков — 3')


def test_build_summary_renewal_move_names_stages():
    events = [_ev('renewal_deal', 'update', {'student_id': 3, 'cycle_no': 2, 'stage_id': 2},
                  {'stage_id': [1, 2]})]
    assert build_summary('renewal.move', events, _RLK) == (
        'Продление Иван Тестов (цикл 2): Урок 4 → Думает')


def test_build_summary_twofa_setup():
    from apps.changelog.labels import resolve_operation
    assert resolve_operation('POST', '/api/auth/2fa/setup') == 'account.twofa_setup'
    events = [_ev('account', 'update', {'email': 'a@test.local', 'twofa_method': 'totp'},
                  {'twofa_method': [None, 'totp']})]
    assert build_summary('account.twofa_setup', events, _RLK) == (
        'Начата настройка 2FA (приложение): a@test.local')


def test_build_summary_student_update_hides_personal_values():
    events = [_ev('student', 'update', {'full_name': 'Иван Тестов', 'phone': '+70000000000'},
                  {'phone': ['+71111111111', '+70000000000']})]
    out = build_summary('student.update', events, _RLK)
    assert out == 'Изменена карточка ученика Иван Тестов: телефон'
    assert '+7' not in out


# ---------------------------------------------------------------------------
# Мотивация конфиденциальна (решение владельца 2026-10-23): ленту видит и
# admin, поэтому ни сумм, ни ставок, ни процентов в описаниях быть не должно.
# ---------------------------------------------------------------------------

_SECRET = ['1000', '1 000', '1\u202f000', '600', '700', '150', '₽', '%']


def _assert_no_amounts(text):
    for s in _SECRET:
        assert s not in text, (s, text)


@pytest.mark.parametrize('operation, events', [
    ('lesson.submit', [
        _ev('lesson', 'insert', {'group_id': 7, 'lesson_number': 5, 'lesson_date': '2026-07-09'}),
        _ev('payroll', 'insert', {'teacher_id': 1, 'lesson_id': 50, 'payment': 700.0})]),
    ('plan.cancel', [
        _ev('planned_lesson', 'update', {'group_id': 7, 'lesson_number': 5,
                                         'scheduled_date': '2026-07-09', 'scheduled_time': '12:00'},
            {'status': ['pending', 'cancelled']}),
        _ev('cancellation_pay', 'insert', {'teacher_id': 1, 'group_id': 7, 'payment': 150.0})]),
    ('payroll.cancellation_update', [
        _ev('cancellation_pay', 'update', {'teacher_id': 1, 'group_id': 7, 'payment': 700.0,
                                           'lesson_date': '2026-07-09', 'scheduled_time': '12:00'},
            {'payment': [150.0, 700.0]})]),
    ('other', [_ev('payroll', 'update', {'teacher_id': 1, 'lesson_id': 50, 'payment': 700.0},
                   {'payment': [600.0, 700.0]})]),
    ('payroll.recalculate', _payrolls(3)),
])
def test_feed_never_shows_motivation_amounts(operation, events):
    _assert_no_amounts(build_summary(operation, events, _RLK))


def test_admin_cannot_revert_motivation_operation(admin_client, superadmin_client):
    from apps.changelog import labels
    assert labels.is_motivation_operation('payroll.update')
    assert not labels.is_motivation_operation('lesson.submit')
    d = Direction.objects.create(name='__motiv_rev__')
    with pghistory.context(url='/api/admin/payroll/1', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__motiv_rev2__')
    row = admin_client.get('/api/admin/changelog?page_size=1').json()['rows'][0]
    assert row['operation'] == 'payroll.update'
    assert row['revertable'] is False
    resp = admin_client.post(f"/api/admin/changelog/{row['id']}/revert")
    assert resp.status_code == 400
    assert 'суперадмину' in resp.json()['error']
    super_row = superadmin_client.get('/api/admin/changelog?page_size=1').json()['rows'][0]
    assert super_row['revertable'] is True
