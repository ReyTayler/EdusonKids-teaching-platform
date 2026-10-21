"""
API «Настройки → Мотивация» и начислений (спека 2026-10-20-eduson-motivation-payroll-design, §4).

Проверяем поведение через HTTP: RBAC, валидацию, пересечения персональных
диапазонов, пересчёт после каждой мутации (число и суммы), удаление версии,
«уроки без ставки», сброс ручной правки, объединённый список уроков и отмен,
кабинет преподавателя с отменами.

Данные выдуманные; даты — в 2031 году: пересчёт по периоду охватывает всю
школу, а в общей тестовой БД там нет чужих уроков и отмен.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.payroll.models import CancellationPay, Payroll, PayRateVersion, PersonalRate
from apps.payroll.tests.conftest import (  # noqa: F401 — фикстура db_school
    D, db_lesson, db_marker, db_school, db_version, recalc_group,
)
from conftest import make_auth_client

pytestmark = pytest.mark.django_db

BASE = '/api/admin/payroll'
VERSIONS = f'{BASE}/rate-versions'
PERSONAL = f'{BASE}/personal-rates'

d = datetime.date


def _payment(lesson) -> Decimal:
    return Payroll.objects.get(lesson=lesson).payment


@pytest.fixture
def motivation_admin_client(db):
    """Свой superadmin внутри транзакции теста (откатится вместе с ней).

    Корневая фикстура удаляет учётку сырым DELETE до отката — а ставки, созданные
    через API, ссылаются на неё (created_by), и проверка отложенных FK в конце
    теста падает."""
    from apps.accounts.models import Account
    account = Account.objects.create(email='motivation-admin@test.local', role='superadmin',
                                     is_active=True, full_name='Тест Мотивация Админ')
    return make_auth_client(account)


def _version_body(effective_from='2031-03-05', **overrides):
    body = {
        'effective_from': effective_from, 'group_rate': '600', 'individual_rate': '800',
        'cancel_mode': 'percent', 'cancel_value': '50',
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

_ENDPOINTS = [
    ('get', VERSIONS), ('post', VERSIONS),
    ('patch', f'{VERSIONS}/1'), ('delete', f'{VERSIONS}/1'),
    ('get', PERSONAL), ('post', PERSONAL),
    ('patch', f'{PERSONAL}/1'), ('delete', f'{PERSONAL}/1'),
    ('post', f'{BASE}/1/reset'),
    ('patch', f'{BASE}/cancellations/1'), ('post', f'{BASE}/cancellations/1/reset'),
]


@pytest.mark.parametrize('method,url', _ENDPOINTS)
def test_rbac_only_superadmin(method, url, anon_client, teacher_client, manager_client,
                              admin_client):
    assert getattr(anon_client, method)(url, {}, format='json').status_code == 401
    for client in (teacher_client, manager_client, admin_client):
        assert getattr(client, method)(url, {}, format='json').status_code == 403


def test_rbac_superadmin_reads(motivation_admin_client, db_school):
    assert motivation_admin_client.get(VERSIONS).status_code == 200
    assert motivation_admin_client.get(PERSONAL).status_code == 200


# ---------------------------------------------------------------------------
# Версии ставок
# ---------------------------------------------------------------------------

def test_create_version_recalculates_its_period(motivation_admin_client, db_school):
    early = db_lesson(db_school, d(2031, 3, 2), 1)
    late = db_lesson(db_school, d(2031, 3, 10), 2)
    recalc_group(db_school)
    assert _payment(late) == D('0')

    resp = motivation_admin_client.post(VERSIONS, _version_body(), format='json')

    assert resp.status_code == 201, resp.json()
    body = resp.json()
    # Пересчитан только урок с даты версии: раньше неё ставки по-прежнему нет.
    assert body['recalculated'] == 1
    assert body['group_rate'] == '600.00'
    assert body['created_by_name'] == 'Тест Мотивация Админ'
    assert _payment(late) == D('600.00')
    assert _payment(early) == D('0.00')
    assert Payroll.objects.get(lesson=early).rule_note.startswith('Нет ставки')


def test_create_version_stops_before_next_version(motivation_admin_client, db_school):
    db_version(d(2031, 4, 1), group_rate=D('900'))
    inside = db_lesson(db_school, d(2031, 3, 10), 1)
    after = db_lesson(db_school, d(2031, 4, 5), 2)
    recalc_group(db_school)

    resp = motivation_admin_client.post(VERSIONS, _version_body(), format='json')

    assert resp.json()['recalculated'] == 1
    assert _payment(inside) == D('600.00')
    assert _payment(after) == D('900.00')


def test_list_versions_shape_and_directions(motivation_admin_client, db_school):
    direction = db_school['direction']
    db_version(d(2031, 1, 1), direction=direction, dir_group=D('700'))

    body = motivation_admin_client.get(VERSIONS).json()

    assert set(body) == {'results', 'no_rate', 'directions'}
    assert direction.id in {x['id'] for x in body['directions']}
    version = body['results'][0]
    assert version['effective_from'] == '2031-01-01'
    cells = {c['direction_id']: c for c in version['directions']}
    # Ячейки — для каждого направления таблицы; пустые — null («как по умолчанию»).
    assert set(cells) == {x['id'] for x in body['directions']}
    assert cells[direction.id]['group_rate'] == '700.00'
    assert cells[direction.id]['individual_rate'] is None


def test_versions_sorted_newest_first(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    db_version(d(2031, 6, 1))
    dates = [v['effective_from'] for v in motivation_admin_client.get(VERSIONS).json()['results']]
    assert dates == ['2031-06-01', '2031-01-01']


def test_no_rate_counts_lessons_before_first_version(motivation_admin_client, db_school):
    db_lesson(db_school, d(1999, 12, 31), 1)
    db_lesson(db_school, d(1999, 12, 30), 2, lesson_type='burned')  # сгорание не в счёт
    db_version(d(2000, 1, 1))

    no_rate = motivation_admin_client.get(VERSIONS).json()['no_rate']

    assert no_rate == {'lessons': 1, 'earliest': '1999-12-31'}


def test_no_rate_without_versions_counts_all_lessons(motivation_admin_client, db_school):
    from apps.lessons.models import Lesson
    db_lesson(db_school, d(2031, 3, 10), 1)
    expected = Lesson.objects.exclude(lesson_type='burned').count()

    no_rate = motivation_admin_client.get(VERSIONS).json()['no_rate']

    assert no_rate['lessons'] == expected >= 1


def test_directions_written_with_version_and_upserted(motivation_admin_client, db_school):
    direction = db_school['direction']
    lesson = db_lesson(db_school, d(2031, 3, 10), 1)
    resp = motivation_admin_client.post(VERSIONS, _version_body(
        directions=[{'direction_id': direction.id, 'group_rate': '700'}]), format='json')
    assert resp.status_code == 201, resp.json()
    version_id = resp.json()['id']
    assert _payment(lesson) == D('700.00')

    # null/null — «как по умолчанию»: строка направления уходит, урок — по 600.
    resp = motivation_admin_client.patch(f'{VERSIONS}/{version_id}', {'directions': [
        {'direction_id': direction.id, 'group_rate': None, 'individual_rate': None}]},
        format='json')

    assert resp.status_code == 200, resp.json()
    assert resp.json()['recalculated'] == 1
    assert _payment(lesson) == D('600.00')
    assert not PayRateVersion.objects.get(id=version_id).directions.exists()


def test_patch_without_directions_keeps_them(motivation_admin_client, db_school):
    direction = db_school['direction']
    version = db_version(d(2031, 3, 1), direction=direction, dir_group=D('700'))

    resp = motivation_admin_client.patch(f'{VERSIONS}/{version.id}', {'group_rate': '650'},
                                   format='json')

    assert resp.status_code == 200
    assert version.directions.get().group_rate == D('700.00')


def test_patch_date_earlier_recalculates_from_new_date(motivation_admin_client, db_school):
    version = db_version(d(2031, 3, 5))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    recalc_group(db_school)
    assert _payment(lesson) == D('0')

    resp = motivation_admin_client.patch(f'{VERSIONS}/{version.id}',
                                   {'effective_from': '2031-03-01'}, format='json')

    assert resp.status_code == 200, resp.json()
    assert resp.json()['recalculated'] == 1
    assert _payment(lesson) == D('600.00')


def test_patch_date_later_recalculates_old_period(motivation_admin_client, db_school):
    version = db_version(d(2031, 3, 1))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    recalc_group(db_school)
    assert _payment(lesson) == D('600.00')

    resp = motivation_admin_client.patch(f'{VERSIONS}/{version.id}',
                                   {'effective_from': '2031-03-05'}, format='json')

    assert resp.json()['recalculated'] == 1
    assert _payment(lesson) == D('0.00')


def test_delete_version_falls_back_to_previous(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1), group_rate=D('500'))
    newer = db_version(d(2031, 3, 5), group_rate=D('600'))
    before = db_lesson(db_school, d(2031, 2, 10), 1)
    lesson = db_lesson(db_school, d(2031, 3, 10), 2)
    recalc_group(db_school)
    assert _payment(lesson) == D('600.00')

    resp = motivation_admin_client.delete(f'{VERSIONS}/{newer.id}')

    assert resp.status_code == 200
    assert resp.json() == {'recalculated': 1}
    assert _payment(lesson) == D('500.00')
    assert _payment(before) == D('500.00')


def test_delete_only_version_leaves_no_rate(motivation_admin_client, db_school):
    version = db_version(d(2031, 3, 1))
    lesson = db_lesson(db_school, d(2031, 3, 10), 1)
    recalc_group(db_school)

    motivation_admin_client.delete(f'{VERSIONS}/{version.id}')

    row = Payroll.objects.get(lesson=lesson)
    assert row.payment == D('0.00')
    assert row.rule_note.startswith('Нет ставки')


@pytest.mark.parametrize('overrides,field', [
    ({'cancel_value': '150'}, 'cancel_value'),
    ({'group_rate': '-1'}, 'group_rate'),
    ({'penalty_amount': '-5'}, 'penalty_amount'),
    ({'cancel_mode': 'bonus'}, 'cancel_mode'),
])
def test_version_validation(motivation_admin_client, db_school, overrides, field):
    resp = motivation_admin_client.post(VERSIONS, _version_body(**overrides), format='json')
    assert resp.status_code == 400
    assert field in resp.json()['details']


def test_fixed_cancel_may_exceed_hundred(motivation_admin_client, db_school):
    resp = motivation_admin_client.post(VERSIONS, _version_body(cancel_mode='fixed',
                                                          cancel_value='350'), format='json')
    assert resp.status_code == 201


def test_patch_mode_to_percent_checks_existing_value(motivation_admin_client, db_school):
    version = db_version(d(2031, 3, 1), cancel_mode='fixed', cancel_value=D('350'))
    resp = motivation_admin_client.patch(f'{VERSIONS}/{version.id}', {'cancel_mode': 'percent'},
                                   format='json')
    assert resp.status_code == 400
    assert 'cancel_value' in resp.json()['details']


def test_duplicate_date_rejected(motivation_admin_client, db_school):
    db_version(d(2031, 3, 5))
    resp = motivation_admin_client.post(VERSIONS, _version_body(), format='json')
    assert resp.status_code == 400
    assert 'уже есть' in resp.json()['details']['effective_from'][0]


def test_unknown_direction_rejected(motivation_admin_client, db_school):
    resp = motivation_admin_client.post(VERSIONS, _version_body(
        directions=[{'direction_id': 999_999_999, 'group_rate': '1'}]), format='json')
    assert resp.status_code == 400
    assert 'directions' in resp.json()['details']


def test_version_404(motivation_admin_client, db_school):
    assert motivation_admin_client.patch(f'{VERSIONS}/999999999', {}, format='json').status_code == 404
    assert motivation_admin_client.delete(f'{VERSIONS}/999999999').status_code == 404


# ---------------------------------------------------------------------------
# Персональные ставки
# ---------------------------------------------------------------------------

def _personal_body(school, **overrides):
    body = {'teacher_id': school['t1'].id, 'group_id': school['group'].id,
            'kind': 'fixed', 'value': '1000'}
    body.update(overrides)
    return body


def test_personal_rate_create_recalculates_group(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    first = db_lesson(db_school, d(2031, 3, 2), 1)
    second = db_lesson(db_school, d(2031, 3, 9), 2)
    recalc_group(db_school)

    resp = motivation_admin_client.post(PERSONAL, _personal_body(
        db_school, lesson_from='2', lesson_to='3', kind='plus_fixed', value='150'),
        format='json')

    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body['recalculated'] == 1
    assert body['teacher_name'] == db_school['t1'].name
    assert body['group_name'] == db_school['group'].name
    assert body['direction_name'] == db_school['direction'].name
    assert 'direction_color' in body
    assert _payment(first) == D('600.00')
    assert _payment(second) == D('750.00')


def test_personal_rate_delete_recalculates_back(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    rate_id = motivation_admin_client.post(PERSONAL, _personal_body(db_school),
                                     format='json').json()['id']
    assert _payment(lesson) == D('1000.00')

    resp = motivation_admin_client.delete(f'{PERSONAL}/{rate_id}')

    assert resp.json() == {'recalculated': 1}
    assert _payment(lesson) == D('600.00')
    assert not PersonalRate.objects.filter(id=rate_id).exists()


def test_personal_rate_patch_group_recalculates_both(motivation_admin_client, db_school):
    from apps.groups.models import Group
    from django.utils import timezone
    db_version(d(2031, 1, 1))
    other = Group.objects.create(
        name='Тест Движок Группа 2', direction=db_school['direction'], teacher=db_school['t1'],
        is_individual=False, lesson_duration_minutes=90, active=True, created_at=timezone.now())
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    rate_id = motivation_admin_client.post(PERSONAL, _personal_body(db_school),
                                     format='json').json()['id']
    assert _payment(lesson) == D('1000.00')

    resp = motivation_admin_client.patch(f'{PERSONAL}/{rate_id}', {'group_id': other.id},
                                   format='json')

    assert resp.status_code == 200, resp.json()
    assert resp.json()['recalculated'] == 1
    assert _payment(lesson) == D('600.00')


@pytest.mark.parametrize('existing,new', [
    ((None, None), (None, None)),            # две «все уроки»
    (('1', '5'), ('5', '8')),                # общая граница
    (('3', '6'), ('1', '10')),               # вложение
])
def test_personal_rate_overlap_rejected(motivation_admin_client, db_school, existing, new):
    first = motivation_admin_client.post(PERSONAL, _personal_body(
        db_school, lesson_from=existing[0], lesson_to=existing[1]), format='json')
    assert first.status_code == 201

    resp = motivation_admin_client.post(PERSONAL, _personal_body(
        db_school, lesson_from=new[0], lesson_to=new[1]), format='json')

    assert resp.status_code == 400
    assert 'non_field_errors' in resp.json()['details']
    assert PersonalRate.objects.filter(group=db_school['group']).count() == 1


def test_personal_rate_all_plus_ranges_allowed(motivation_admin_client, db_school):
    for lesson_from, lesson_to in ((None, None), ('1', '4'), ('4.5', '8')):
        resp = motivation_admin_client.post(PERSONAL, _personal_body(
            db_school, lesson_from=lesson_from, lesson_to=lesson_to), format='json')
        assert resp.status_code == 201, resp.json()
    # Другой преподаватель в той же группе пересечений не создаёт.
    resp = motivation_admin_client.post(PERSONAL, _personal_body(
        db_school, teacher_id=db_school['t2'].id, lesson_from='1', lesson_to='4'),
        format='json')
    assert resp.status_code == 201


def test_personal_rate_patch_into_overlap_rejected(motivation_admin_client, db_school):
    motivation_admin_client.post(PERSONAL, _personal_body(db_school, lesson_from='1', lesson_to='4'),
                           format='json')
    second = motivation_admin_client.post(PERSONAL, _personal_body(
        db_school, lesson_from='5', lesson_to='8'), format='json').json()['id']

    resp = motivation_admin_client.patch(f'{PERSONAL}/{second}', {'lesson_from': '3'}, format='json')

    assert resp.status_code == 400
    assert PersonalRate.objects.get(id=second).lesson_from == D('5.0')


@pytest.mark.parametrize('overrides', [
    {'lesson_from': '3', 'lesson_to': None},
    {'lesson_from': '5', 'lesson_to': '2'},
    {'value': '-1'},
    {'kind': 'bonus'},
    {'teacher_id': 999_999_999},
])
def test_personal_rate_validation(motivation_admin_client, db_school, overrides):
    resp = motivation_admin_client.post(PERSONAL, _personal_body(db_school, **overrides),
                                  format='json')
    assert resp.status_code == 400


def test_personal_rates_list_filter_sort_paginate(motivation_admin_client, db_school):
    for lesson_from, lesson_to in (('5', '8'), (None, None), ('1', '4')):
        motivation_admin_client.post(PERSONAL, _personal_body(
            db_school, lesson_from=lesson_from, lesson_to=lesson_to), format='json')
    motivation_admin_client.post(PERSONAL, _personal_body(db_school, teacher_id=db_school['t2'].id),
                           format='json')

    resp = motivation_admin_client.get(PERSONAL, {'teacher': db_school['t1'].id,
                                            'sort_by': 'lesson_from', 'page_size': 2})

    body = resp.json()
    assert set(body) == {'rows', 'total', 'page', 'page_size'}
    assert body['total'] == 3
    # «Все уроки» первыми, затем диапазоны по возрастанию.
    assert [r['lesson_from'] for r in body['rows']] == [None, '1.0']
    page2 = motivation_admin_client.get(PERSONAL, {'teacher': db_school['t1'].id,
                                             'sort_by': 'lesson_from', 'page_size': 2,
                                             'page': 2}).json()
    assert [r['lesson_from'] for r in page2['rows']] == ['5.0']

    by_group = motivation_admin_client.get(PERSONAL, {'group': db_school['group'].id}).json()
    assert by_group['total'] == 4


@pytest.mark.parametrize('params', [
    {'sort_by': 'value; DROP TABLE x'}, {'sort_dir': 'sideways'}, {'teacher': 'abc'},
])
def test_personal_rates_list_rejects_garbage(motivation_admin_client, db_school, params):
    assert motivation_admin_client.get(PERSONAL, params).status_code == 400


# ---------------------------------------------------------------------------
# Начисления: сброс правки, отмены, объединённый список, сводка
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('field', ['total_students', 'present_count'])
def test_patch_payroll_rejects_headcount(motivation_admin_client, db_school, field):
    """Число учеников и присутствующих выводит пересчёт из посещаемости — правка
    через PATCH была бы молча затёрта, поэтому поле отклоняется, а не глотается."""
    db_version(d(2031, 1, 1))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    recalc_group(db_school)
    row = Payroll.objects.get(lesson=lesson)

    resp = motivation_admin_client.patch(f'{BASE}/{row.id}', {field: 9, 'payment': 999},
                                         format='json')

    assert resp.status_code == 400
    assert field in resp.json()['details']
    row.refresh_from_db()
    assert (row.total_students, row.present_count) == (3, 2)
    assert (row.payment, row.is_manual) == (D('600.00'), False)


def test_reset_manual_lesson_payroll(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    recalc_group(db_school)
    row = Payroll.objects.get(lesson=lesson)
    patched = motivation_admin_client.patch(f'{BASE}/{row.id}', {'payment': 999}, format='json')
    assert patched.json()['is_manual'] is True
    # Ручная правка переживает пересчёт.
    recalc_group(db_school)
    assert _payment(lesson) == D('999.00')

    resp = motivation_admin_client.post(f'{BASE}/{row.id}/reset')

    assert resp.status_code == 200
    body = resp.json()
    assert body['is_manual'] is False
    assert body['reset'] is True
    assert body['payment'] == '600.00'
    assert body['recalculated'] == 1
    assert _payment(lesson) == D('600.00')


def test_reset_404(motivation_admin_client, db_school):
    assert motivation_admin_client.post(f'{BASE}/999999999/reset').status_code == 404
    assert motivation_admin_client.post(f'{BASE}/cancellations/999999999/reset').status_code == 404
    assert motivation_admin_client.patch(f'{BASE}/cancellations/999999999', {'payment': 1},
                                   format='json').status_code == 404


def test_cancellation_manual_edit_and_reset(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    db_marker(db_school, d(2031, 3, 4))
    recalc_group(db_school)
    pay = CancellationPay.objects.get(group=db_school['group'])
    assert pay.payment == D('300.00')

    resp = motivation_admin_client.patch(f'{BASE}/cancellations/{pay.id}', {'payment': '450'},
                                   format='json')
    assert resp.status_code == 200
    assert resp.json()['is_manual'] is True
    assert resp.json()['payment'] == '450.00'
    recalc_group(db_school)
    assert CancellationPay.objects.get(id=pay.id).payment == D('450.00')

    resp = motivation_admin_client.post(f'{BASE}/cancellations/{pay.id}/reset')

    assert resp.status_code == 200
    assert resp.json()['payment'] == '300.00'
    assert resp.json()['is_manual'] is False


def test_cancellation_patch_validation(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    db_marker(db_school, d(2031, 3, 4))
    recalc_group(db_school)
    pay = CancellationPay.objects.get(group=db_school['group'])
    for body in ({'payment': '-1'}, {}, {'payment': 'abc'}):
        assert motivation_admin_client.patch(f'{BASE}/cancellations/{pay.id}', body,
                                       format='json').status_code == 400


def _school_entries(db_school):
    db_version(d(2031, 1, 1))
    db_lesson(db_school, d(2031, 3, 2), 1)
    db_lesson(db_school, d(2031, 3, 9), 2, present=(True,))
    db_marker(db_school, d(2031, 3, 4))
    recalc_group(db_school)
    # Ставка t1 «вместо ставки» на урок 2 — чтобы суммы различались для сортировки.
    PersonalRate.objects.create(teacher=db_school['t1'], group=db_school['group'],
                                lesson_from=D('2'), lesson_to=D('2'), kind='fixed',
                                value=D('1000'))
    recalc_group(db_school)


def test_list_unions_lessons_and_cancellations(motivation_admin_client, db_school):
    _school_entries(db_school)
    teacher = db_school['t1'].id

    body = motivation_admin_client.get(BASE, {'filter[teacher_id]': teacher}).json()

    assert body['total'] == 3
    # По умолчанию — дата по убыванию.
    assert [(r['kind'], r['lesson_date']) for r in body['rows']] == [
        ('lesson', '2031-03-09'), ('cancellation', '2031-03-04'), ('lesson', '2031-03-02')]
    cancel = body['rows'][1]
    assert cancel['lesson_id'] is None
    assert cancel['payment'] == '300.00'
    assert cancel['penalty'] == '0.00'
    assert cancel['scheduled_time'] == '15:00:00'
    assert cancel['group_name'] == db_school['group'].name
    assert cancel['rule_note'].startswith('Групповой урок')
    lesson = body['rows'][0]
    assert {'is_manual', 'calc_payment', 'calc_penalty', 'rule_note'} <= set(lesson)


def test_list_union_sort_and_pagination(motivation_admin_client, db_school):
    _school_entries(db_school)
    params = {'filter[teacher_id]': db_school['t1'].id, 'sort_by': 'payment',
              'sort_dir': 'asc', 'page_size': 2}

    page1 = motivation_admin_client.get(BASE, params).json()
    page2 = motivation_admin_client.get(BASE, {**params, 'page': 2}).json()

    assert page1['total'] == page2['total'] == 3
    assert [r['payment'] for r in page1['rows']] == ['300.00', '600.00']
    assert [r['payment'] for r in page2['rows']] == ['1000.00']


def test_list_filter_kind(motivation_admin_client, db_school):
    _school_entries(db_school)
    base = {'filter[teacher_id]': db_school['t1'].id}
    only_cancel = motivation_admin_client.get(BASE, {**base, 'filter[kind]': 'cancellation'}).json()
    only_lessons = motivation_admin_client.get(BASE, {**base, 'filter[kind]': 'lesson'}).json()
    assert [r['kind'] for r in only_cancel['rows']] == ['cancellation']
    assert only_lessons['total'] == 2


@pytest.mark.parametrize('params', [
    {'sort_by': '; DROP TABLE payroll; --'}, {'sort_dir': 'up'}, {'filter[kind]': 'bonus'},
    {'filter[teacher_id]': 'x'},
])
def test_list_rejects_garbage(motivation_admin_client, db_school, params):
    assert motivation_admin_client.get(BASE, params).status_code == 400


def test_summary_includes_cancellations(motivation_admin_client, db_school):
    _school_entries(db_school)

    rows = motivation_admin_client.get(f'{BASE}/summary',
                                 {'teacher_id': db_school['t1'].id}).json()

    assert len(rows) == 1
    row = rows[0]
    assert row['lessons_count'] == '2'
    assert row['cancellations_count'] == '1'
    assert row['sum_lessons_payment'] == '1600.00'
    assert row['sum_cancellations_payment'] == '300.00'
    assert row['sum_payment'] == '1900.00'


def test_summary_teacher_with_only_cancellations(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    db_marker(db_school, d(2031, 3, 4), teacher=db_school['t2'])
    recalc_group(db_school)

    rows = motivation_admin_client.get(f'{BASE}/summary', {'teacher_id': db_school['t2'].id}).json()

    assert rows[0]['lessons_count'] == '0'
    assert rows[0]['sum_payment'] == '300.00'


# ---------------------------------------------------------------------------
# Кабинет преподавателя и карточка урока
# ---------------------------------------------------------------------------

def _teacher_client(teacher):
    from apps.accounts.models import Account
    account = Account.objects.create(
        email=f'motivation-{teacher.id}@test.local', role='teacher', teacher=teacher,
        is_active=True)
    return make_auth_client(account)


def test_my_payroll_shows_own_cancellations_only(db_school):
    db_version(d(2031, 1, 1))
    db_lesson(db_school, d(2031, 3, 2), 1)
    db_marker(db_school, d(2031, 3, 4))
    db_marker(db_school, d(2031, 3, 11), teacher=db_school['t2'])
    recalc_group(db_school)

    body = _teacher_client(db_school['t1']).get('/api/my-payroll', {'month': '2031-03'}).json()

    kinds = [(r['kind'], r['date']) for r in body['rows']]
    assert kinds == [('cancellation', '2031-03-04'), ('regular', '2031-03-02')]
    cancel = body['rows'][0]
    lesson_row = body['rows'][1]
    # Единая форма строк: у урока нет отмены и времени, у отмены — урока.
    assert lesson_row['cancellationId'] is None and lesson_row['time'] is None
    assert cancel['cancellationId'] is not None and cancel['time'] == '15:00:00'
    assert cancel['payment'] == '300.00'
    assert cancel['ruleNote'].startswith('Групповой урок')
    assert cancel['lessonId'] is None
    assert body['totals']['lessons'] == 1
    assert body['totals']['cancellations'] == 1
    assert body['totals']['payment'] == '900.00'
    assert body['totals']['net'] == '900.00'


def test_lesson_card_payroll_has_rule_fields(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    recalc_group(db_school)

    payroll = motivation_admin_client.get(f'/api/admin/lessons/{lesson.id}').json()['payroll']

    assert payroll['is_manual'] is False
    assert payroll['calc_payment'] == '600.00'
    assert payroll['calc_penalty'] == '0.00'
    assert payroll['rule_note'].startswith('Групповой урок')


# ---------------------------------------------------------------------------
# Журнал изменений: операции новых мутаций
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('method,url,operation', [
    ('POST', VERSIONS, 'payroll.rate_version_create'),
    ('PATCH', f'{VERSIONS}/3', 'payroll.rate_version_update'),
    ('DELETE', f'{VERSIONS}/3', 'payroll.rate_version_delete'),
    ('POST', PERSONAL, 'payroll.personal_rate_create'),
    ('PATCH', f'{PERSONAL}/3', 'payroll.personal_rate_update'),
    ('DELETE', f'{PERSONAL}/3', 'payroll.personal_rate_delete'),
    ('POST', f'{BASE}/3/reset', 'payroll.reset'),
    ('PATCH', f'{BASE}/3', 'payroll.update'),
    ('PATCH', f'{BASE}/cancellations/3', 'payroll.cancellation_update'),
    ('POST', f'{BASE}/cancellations/3/reset', 'payroll.cancellation_reset'),
    ('COMMAND', 'manual/recalculate-payroll', 'payroll.recalculate'),
])
def test_changelog_operations(method, url, operation):
    from apps.changelog.labels import resolve_operation
    assert resolve_operation(method, url) == operation


# ---------------------------------------------------------------------------
# Ревью задачи 5: замок версий, ограничения БД, сироты, текущая версия
# ---------------------------------------------------------------------------

def _first_index(queries, needle):
    return next(i for i, q in enumerate(queries) if needle in q['sql'])


@pytest.mark.parametrize('action', ['create', 'update', 'delete'])
def test_version_mutations_lock_before_reading_neighbours(motivation_admin_client, db_school,
                                                          action):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    db_version(d(2031, 1, 1))
    target = db_version(d(2031, 3, 5))
    db_version(d(2031, 6, 1))
    calls = {
        'create': lambda: motivation_admin_client.post(
            VERSIONS, _version_body('2031-04-01'), format='json'),
        'update': lambda: motivation_admin_client.patch(
            f'{VERSIONS}/{target.id}', {'effective_from': '2031-03-06'}, format='json'),
        'delete': lambda: motivation_admin_client.delete(f'{VERSIONS}/{target.id}'),
    }
    with CaptureQueriesContext(connection) as ctx:
        assert calls[action]().status_code in (200, 201)

    lock = _first_index(ctx.captured_queries, 'pg_advisory_xact_lock')
    neighbours = _first_index(ctx.captured_queries,
                              '"payroll_rate_versions"."effective_from" >')
    assert lock < neighbours


def test_version_check_violation_is_explained_not_duplicate(db_school):
    """CHECK в БД (сериализатор обойдён — гонка/прямой вызов) → 400 со своим
    текстом, а не «версия уже есть»."""
    from rest_framework.exceptions import ValidationError

    from apps.payroll import motivation
    with pytest.raises(ValidationError) as exc:
        motivation.create_version({
            'effective_from': d(2031, 3, 5), 'group_rate': D('600'),
            'individual_rate': D('800'), 'cancel_mode': 'percent',
            'cancel_value': D('150')}, None)
    assert exc.value.detail == {'cancel_value': [motivation.CANCEL_PERCENT_LIMIT]}


def test_personal_range_check_violation_is_400_not_500(db_school):
    from rest_framework.exceptions import ValidationError

    from apps.payroll import motivation
    with pytest.raises(ValidationError) as exc:
        motivation.create_personal_rate({
            'teacher_id': db_school['t1'].id, 'group_id': db_school['group'].id,
            'lesson_from': D('5'), 'lesson_to': D('2'), 'kind': 'fixed',
            'value': D('1')}, None)
    assert exc.value.detail == {'lesson_to': [motivation.RANGE_ORDER]}


@pytest.mark.parametrize('first,second,constraint', [
    ((None, None), (None, None), 'payroll_personal_rates_all_lessons_uniq'),
    ((D('1'), D('5')), (D('4.5'), D('9')), 'payroll_personal_rates_ranges_excl'),
])
def test_overlap_held_by_database(db_school, first, second, constraint):
    """Непересечение держит сама БД — и для записи мимо сервиса."""
    from django.db import IntegrityError, transaction
    kwargs = {'teacher': db_school['t1'], 'group': db_school['group'], 'kind': 'fixed',
              'value': D('1')}
    PersonalRate.objects.create(lesson_from=first[0], lesson_to=first[1], **kwargs)
    with pytest.raises(IntegrityError) as exc, transaction.atomic():
        PersonalRate.objects.create(lesson_from=second[0], lesson_to=second[1], **kwargs)
    assert exc.value.__cause__.diag.constraint_name == constraint


def test_overlap_text_names_conflicting_range(motivation_admin_client, db_school):
    motivation_admin_client.post(PERSONAL, _personal_body(
        db_school, lesson_from='1', lesson_to='5'), format='json')
    resp = motivation_admin_client.post(PERSONAL, _personal_body(
        db_school, lesson_from='4.5', lesson_to='9'), format='json')
    assert resp.status_code == 400
    assert resp.json()['details']['non_field_errors'] == [
        'Уроки 4.5–9 пересекаются с уже заданными 1–5 у этого преподавателя в этой группе.']


def test_personal_rate_patch_teacher_recalculates(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)  # провёл t1
    rate_id = motivation_admin_client.post(PERSONAL, _personal_body(db_school),
                                           format='json').json()['id']
    assert _payment(lesson) == D('1000.00')

    resp = motivation_admin_client.patch(f'{PERSONAL}/{rate_id}',
                                         {'teacher_id': db_school['t2'].id}, format='json')

    assert resp.status_code == 200, resp.json()
    assert resp.json()['teacher_name'] == db_school['t2'].name
    # Ставка t2 на урок t1 не действует — урок обратно по ставке формата.
    assert resp.json()['recalculated'] == 1
    assert _payment(lesson) == D('600.00')


def test_version_recalculated_counts_cancellations(motivation_admin_client, db_school):
    lesson = db_lesson(db_school, d(2031, 3, 10), 1)
    db_marker(db_school, d(2031, 3, 12))
    recalc_group(db_school)
    pay = CancellationPay.objects.get(group=db_school['group'])
    assert pay.payment == D('0')

    resp = motivation_admin_client.post(VERSIONS, _version_body(), format='json')

    assert resp.json()['recalculated'] == 2
    assert _payment(lesson) == D('600.00')
    assert CancellationPay.objects.get(id=pay.id).payment == D('300.00')


def test_reset_orphan_cancellation(motivation_admin_client, db_school):
    from apps.payroll.tests.conftest import db_orphan
    orphan = db_orphan(db_school, d(2024, 3, 4), payment='300')
    CancellationPay.objects.filter(id=orphan.id).update(payment=D('999'), is_manual=True)

    resp = motivation_admin_client.post(f'{BASE}/cancellations/{orphan.id}/reset')

    assert resp.status_code == 200
    body = resp.json()
    assert body['payment'] == '300.00'
    assert body['is_manual'] is False
    assert body['reset'] is True
    assert body['recalculated'] == 0
    assert body['planned_lesson_id'] is None


def test_reset_not_manual_reports_reset_false(motivation_admin_client, db_school):
    db_version(d(2031, 1, 1))
    lesson = db_lesson(db_school, d(2031, 3, 2), 1)
    recalc_group(db_school)
    row = Payroll.objects.get(lesson=lesson)

    body = motivation_admin_client.post(f'{BASE}/{row.id}/reset').json()

    assert body['reset'] is False
    assert body['recalculated'] == 0


def test_is_current_marks_version_in_force_today(motivation_admin_client, db_school):
    past = db_version(d(2020, 1, 1))
    current = db_version(d(2021, 1, 1))
    future = db_version(d(2099, 1, 1))

    results = motivation_admin_client.get(VERSIONS).json()['results']

    flags = {v['id']: v['is_current'] for v in results}
    assert flags == {past.id: False, current.id: True, future.id: False}


def test_list_lesson_number_desc_puts_cancellations_last(motivation_admin_client, db_school):
    _school_entries(db_school)
    rows = motivation_admin_client.get(BASE, {
        'filter[teacher_id]': db_school['t1'].id, 'sort_by': 'lesson_number',
        'sort_dir': 'desc'}).json()['rows']
    assert [r['kind'] for r in rows] == ['lesson', 'lesson', 'cancellation']
    assert [r['lesson_number'] for r in rows[:2]] == ['2.0', '1.0']


@pytest.mark.parametrize('url,params', [
    (BASE, {'filter[date_from]': 'вчера'}),
    (BASE, {'filter[date_to]': '2031-13-40'}),
    (f'{BASE}/summary', {'date_from': 'abc'}),
    (f'{BASE}/summary', {'date_to': '2031/03/01'}),
])
def test_date_filters_reject_garbage(motivation_admin_client, db_school, url, params):
    assert motivation_admin_client.get(url, params).status_code == 400
