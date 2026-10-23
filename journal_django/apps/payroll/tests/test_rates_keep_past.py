"""
Поправка владельца 2026-10-22 (спека 2026-10-20-eduson-motivation-payroll-design, §8):
изменения ставок не меняют прошлое.

Персональные ставки: создать / изменить / выключить / включить / удалить действуют
с сегодняшнего дня по МСК; начисления за более ранние даты остаются прежними и
сразу, и при любом последующем пересчёте (полном, по группе — как мягкое
обновление), потому что история хранится периодами (PersonalRateTerm).
Ставки школы: новая версия — не раньше сегодня; начавшуюся не правят и не удаляют.

«Сегодня» подменяется фикстурой clock: сервис ставок и пересчёт берут его из
msk_now своих модулей. Даты — 2031 год: в общей тестовой БД там нет чужих уроков.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core.utils.dates import MSK
from apps.groups.models import Group
from apps.payroll import motivation
from apps.payroll.models import CancellationPay, Payroll, PersonalRate, PersonalRateTerm
from apps.payroll.recalc import recalculate
from apps.payroll.tests.conftest import (  # noqa: F401 — фикстура db_school
    D, db_lesson, db_marker, db_personal, db_school, db_version, recalc_group,
)
from conftest import make_auth_client

pytestmark = pytest.mark.django_db

BASE = '/api/admin/payroll'
VERSIONS = f'{BASE}/rate-versions'
PERSONAL = f'{BASE}/personal-rates'

d = datetime.date


@pytest.fixture
def client(db):
    from apps.accounts.models import Account
    account = Account.objects.create(email='keep-past-admin@test.local', role='superadmin',
                                     is_active=True, full_name='Тест Прошлое Админ')
    return make_auth_client(account)


@pytest.fixture
def clock(monkeypatch):
    """Переводит «сегодня» (МСК) сервиса ставок и пересчёта на заданную дату."""
    def set_today(day: datetime.date) -> None:
        now = datetime.datetime.combine(day, datetime.time(12, 0), tzinfo=MSK)
        monkeypatch.setattr('apps.payroll.motivation.msk_now', lambda: now)
        monkeypatch.setattr('apps.payroll.recalc.msk_now', lambda: now)
    return set_today


def _pay(lesson) -> D:
    return Payroll.objects.get(lesson=lesson).payment


def _body(school, **overrides):
    body = {'teacher_id': school['t1'].id, 'group_id': school['group'].id,
            'kind': 'fixed', 'value': '1000'}
    body.update(overrides)
    return body


def _terms(rate_id) -> list[tuple]:
    return list(PersonalRateTerm.objects.filter(rate_id=rate_id)
                .order_by(F('valid_from').asc(nulls_first=True), 'id')
                .values_list('valid_from', 'valid_until', 'kind', 'value'))


def _full_recalcs(school):
    """Пересчёты, которые не должны трогать прошлое: по группе (как мягкое
    обновление sync_eduson) и полный — без фильтров."""
    recalc_group(school)
    recalculate()


# ---------------------------------------------------------------------------
# Персональные ставки: правка
# ---------------------------------------------------------------------------

def test_edit_applies_from_today_and_keeps_past(client, db_school, clock):
    db_version(d(2031, 1, 1))
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    past = db_lesson(db_school, d(2031, 3, 5), 1)
    future = db_lesson(db_school, d(2031, 3, 20), 2)
    recalc_group(db_school)
    assert (_pay(past), _pay(future)) == (D('1000.00'), D('1000.00'))

    clock(d(2031, 3, 15))
    resp = client.patch(f'{PERSONAL}/{rate_id}', {'value': '1500'}, format='json')

    assert resp.status_code == 200, resp.json()
    body = resp.json()
    # Пересчитан только урок с сегодняшнего дня.
    assert body['recalculated'] == 1
    assert (body['value'], body['valid_from'], body['valid_until']) == (
        '1500.00', '2031-03-15', None)
    assert [(t['valid_from'], t['valid_until'], t['value']) for t in body['terms']] == [
        ('2031-03-01', '2031-03-15', '1000.00'), ('2031-03-15', None, '1500.00')]
    assert (_pay(past), _pay(future)) == (D('1000.00'), D('1500.00'))

    _full_recalcs(db_school)
    assert (_pay(past), _pay(future)) == (D('1000.00'), D('1500.00'))


def test_edit_on_creation_day_changes_term_in_place(client, db_school, clock):
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']

    resp = client.patch(f'{PERSONAL}/{rate_id}', {'kind': 'plus_fixed', 'value': '200'},
                        format='json')

    assert resp.status_code == 200, resp.json()
    # Без пустого периода [01.03, 01.03): начатый сегодня правится на месте.
    assert _terms(rate_id) == [(d(2031, 3, 1), None, 'plus_fixed', D('200.00'))]


def test_note_edit_does_not_open_new_term(client, db_school, clock):
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    clock(d(2031, 3, 15))

    resp = client.patch(f'{PERSONAL}/{rate_id}', {'note': 'договорённость', 'value': '1000'},
                        format='json')

    assert resp.status_code == 200, resp.json()
    assert resp.json()['note'] == 'договорённость'
    assert resp.json()['recalculated'] == 0
    assert _terms(rate_id) == [(d(2031, 3, 1), None, 'fixed', D('1000.00'))]


def test_migrated_term_without_start_closes_today(client, db_school, clock):
    """Ставка из времени до поправки: период «с начала» закрывается сегодня,
    уроки до сегодня остаются по нему."""
    db_version(d(2031, 1, 1))
    rate = db_personal(db_school, value='900')
    past = db_lesson(db_school, d(2031, 3, 5), 1)
    future = db_lesson(db_school, d(2031, 3, 20), 2)
    recalc_group(db_school)
    clock(d(2031, 3, 15))

    client.patch(f'{PERSONAL}/{rate.id}', {'value': '1100'}, format='json')

    assert _terms(rate.id) == [(None, d(2031, 3, 15), 'fixed', D('900.00')),
                               (d(2031, 3, 15), None, 'fixed', D('1100.00'))]
    _full_recalcs(db_school)
    assert (_pay(past), _pay(future)) == (D('900.00'), D('1100.00'))


def test_edit_of_inactive_rate_rejected(client, db_school, clock):
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    client.post(f'{PERSONAL}/{rate_id}/deactivate')

    resp = client.patch(f'{PERSONAL}/{rate_id}', {'value': '5'}, format='json')

    assert resp.status_code == 400
    assert resp.json()['details']['non_field_errors'] == [motivation.INACTIVE_EDIT]
    # Примечание у выключенной править можно — на начисления оно не влияет.
    assert client.patch(f'{PERSONAL}/{rate_id}', {'note': 'пауза'},
                        format='json').status_code == 200


# ---------------------------------------------------------------------------
# Переключатель «включена / выключена»
# ---------------------------------------------------------------------------

def test_deactivate_then_activate_school_rate_in_between(client, db_school, clock):
    db_version(d(2031, 1, 1))
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    before = db_lesson(db_school, d(2031, 3, 5), 1)
    off = db_lesson(db_school, d(2031, 3, 12), 2)
    after = db_lesson(db_school, d(2031, 3, 20), 3)
    marker_off = db_marker(db_school, d(2031, 3, 13))
    recalc_group(db_school)

    clock(d(2031, 3, 10))
    resp = client.post(f'{PERSONAL}/{rate_id}/deactivate')
    assert resp.status_code == 200, resp.json()
    assert resp.json()['is_active'] is False
    assert resp.json()['recalculated'] == 3  # два урока и отмена с 10.03
    assert (_pay(before), _pay(off), _pay(after)) == (D('1000.00'), D('600.00'), D('600.00'))

    clock(d(2031, 3, 15))
    resp = client.post(f'{PERSONAL}/{rate_id}/activate')
    assert resp.status_code == 200, resp.json()
    assert resp.json()['is_active'] is True
    assert resp.json()['recalculated'] == 1
    assert (_pay(before), _pay(off), _pay(after)) == (D('1000.00'), D('600.00'), D('1000.00'))
    assert _terms(rate_id) == [(d(2031, 3, 1), d(2031, 3, 10), 'fixed', D('1000.00')),
                               (d(2031, 3, 15), None, 'fixed', D('1000.00'))]

    _full_recalcs(db_school)
    assert (_pay(before), _pay(off), _pay(after)) == (D('1000.00'), D('600.00'), D('1000.00'))
    # Отмена в период выключения — по ставке школы: 50 % от 600.
    assert CancellationPay.objects.get(planned_lesson=marker_off).payment == D('300.00')


def test_toggle_repeat_is_idempotent(client, db_school, clock):
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    clock(d(2031, 3, 5))
    client.post(f'{PERSONAL}/{rate_id}/deactivate')
    terms = _terms(rate_id)

    again = client.post(f'{PERSONAL}/{rate_id}/deactivate')
    assert again.status_code == 200
    assert (again.json()['is_active'], again.json()['recalculated']) == (False, 0)
    assert _terms(rate_id) == terms

    client.post(f'{PERSONAL}/{rate_id}/activate')
    terms = _terms(rate_id)
    again = client.post(f'{PERSONAL}/{rate_id}/activate')
    assert (again.json()['is_active'], again.json()['recalculated']) == (True, 0)
    assert _terms(rate_id) == terms


def test_deactivate_on_creation_day_keeps_params_for_activation(client, db_school, clock):
    """Выключили в день создания: период пустой, но параметры не потеряны —
    список показывает их, включение в тот же день открывает тот же период."""
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school, kind='plus_percent', value='15'),
                          format='json').json()['id']

    row = client.post(f'{PERSONAL}/{rate_id}/deactivate').json()

    assert (row['kind'], row['value'], row['valid_from'], row['valid_until']) == (
        'plus_percent', '15.00', '2031-03-01', '2031-03-01')
    client.post(f'{PERSONAL}/{rate_id}/activate')
    assert _terms(rate_id) == [(d(2031, 3, 1), None, 'plus_percent', D('15.00'))]


def test_reactivation_later_uses_last_params(client, db_school, clock):
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    clock(d(2031, 3, 5))
    client.patch(f'{PERSONAL}/{rate_id}', {'value': '1200'}, format='json')
    client.post(f'{PERSONAL}/{rate_id}/deactivate')  # тот же день — период [05.03, 05.03)

    clock(d(2031, 3, 20))
    row = client.post(f'{PERSONAL}/{rate_id}/activate').json()

    assert (row['value'], row['valid_from']) == ('1200.00', '2031-03-20')


# ---------------------------------------------------------------------------
# Удаление
# ---------------------------------------------------------------------------

def test_delete_keeps_past_and_hides_rate(client, db_school, clock):
    db_version(d(2031, 1, 1))
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    past = db_lesson(db_school, d(2031, 3, 5), 1)
    future = db_lesson(db_school, d(2031, 3, 20), 2)
    recalc_group(db_school)

    clock(d(2031, 3, 15))
    resp = client.delete(f'{PERSONAL}/{rate_id}')

    assert resp.json() == {'recalculated': 1}
    assert (_pay(past), _pay(future)) == (D('1000.00'), D('600.00'))
    assert rate_id not in {r['id'] for r in client.get(PERSONAL).json()['rows']}
    _full_recalcs(db_school)
    assert (_pay(past), _pay(future)) == (D('1000.00'), D('600.00'))
    # Удалённая ставка — 404 на любые действия.
    assert client.patch(f'{PERSONAL}/{rate_id}', {'note': 'x'}, format='json').status_code == 404
    assert client.post(f'{PERSONAL}/{rate_id}/activate').status_code == 404
    assert client.delete(f'{PERSONAL}/{rate_id}').status_code == 404


def test_delete_inactive_rate_keeps_its_closed_term(client, db_school, clock):
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    clock(d(2031, 3, 5))
    client.post(f'{PERSONAL}/{rate_id}/deactivate')
    clock(d(2031, 3, 9))

    assert client.delete(f'{PERSONAL}/{rate_id}').status_code == 200
    assert _terms(rate_id) == [(d(2031, 3, 1), d(2031, 3, 5), 'fixed', D('1000.00'))]


# ---------------------------------------------------------------------------
# Замена «с даты»: непересечение по урокам и датам
# ---------------------------------------------------------------------------

def test_overlapping_new_rate_rejected_while_old_active(client, db_school, clock):
    clock(d(2031, 3, 1))
    client.post(PERSONAL, _body(db_school), format='json')
    clock(d(2031, 3, 15))

    resp = client.post(PERSONAL, _body(db_school, value='2000'), format='json')

    assert resp.status_code == 400
    assert resp.json()['details']['non_field_errors'] == [motivation.ALL_LESSONS_TAKEN]


def test_replacement_after_delete_splits_by_date(client, db_school, clock):
    """Старую ставку удалили сегодня, новую на те же уроки завели сегодня —
    прошлое по старой, с сегодня — по новой."""
    db_version(d(2031, 1, 1))
    clock(d(2031, 3, 1))
    old_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    past = db_lesson(db_school, d(2031, 3, 5), 1)
    future = db_lesson(db_school, d(2031, 3, 20), 2)
    recalc_group(db_school)

    clock(d(2031, 3, 15))
    client.delete(f'{PERSONAL}/{old_id}')
    resp = client.post(PERSONAL, _body(db_school, value='2000'), format='json')

    assert resp.status_code == 201, resp.json()
    _full_recalcs(db_school)
    assert (_pay(past), _pay(future)) == (D('1000.00'), D('2000.00'))


def test_range_replacement_after_deactivate_allowed(client, db_school, clock):
    clock(d(2031, 3, 1))
    first = client.post(PERSONAL, _body(db_school, lesson_from='1', lesson_to='5'),
                        format='json').json()['id']
    clock(d(2031, 3, 15))
    client.post(f'{PERSONAL}/{first}/deactivate')

    resp = client.post(PERSONAL, _body(db_school, lesson_from='3', lesson_to='8'), format='json')
    assert resp.status_code == 201, resp.json()

    # Включить старую нельзя: её уроки 1–5 с сегодня пересекаются с новой 3–8.
    resp = client.post(f'{PERSONAL}/{first}/activate')
    assert resp.status_code == 400
    assert resp.json()['details']['non_field_errors'] == [
        'Уроки 1–5 пересекаются с уже заданными 3–8 у этого преподавателя в этой группе.']
    assert PersonalRate.objects.get(id=first).is_active is False


def test_reopen_migrated_term_conflict_is_400(client, db_school, clock):
    """Ставка из времени до поправки (период «с начала») выключена сегодня, на её
    уроки завели новую; включение в тот же день открыло бы старый период обратно —
    пересечение, понятный 400, а не 500."""
    rate = db_personal(db_school, lesson_from='1', lesson_to='5')
    clock(d(2031, 3, 15))
    client.post(f'{PERSONAL}/{rate.id}/deactivate')
    assert client.post(PERSONAL, _body(db_school, lesson_from='4', lesson_to='6'),
                       format='json').status_code == 201

    resp = client.post(f'{PERSONAL}/{rate.id}/activate')

    assert resp.status_code == 400
    assert resp.json()['details']['non_field_errors'] == [
        'Уроки 1–5 пересекаются с уже заданными 4–6 у этого преподавателя в этой группе.']
    assert _terms(rate.id) == [(None, d(2031, 3, 15), 'fixed', D('1000.00'))]


def test_all_lessons_plus_range_still_allowed(client, db_school, clock):
    """§1.6 не отменён: «все уроки» + диапазоны на одни даты — законно,
    диапазон точнее и побеждает."""
    db_version(d(2031, 1, 1))
    clock(d(2031, 3, 1))
    for lesson_from, lesson_to, value in ((None, None, '900'), ('2', '3', '1500')):
        resp = client.post(PERSONAL, _body(db_school, lesson_from=lesson_from,
                                           lesson_to=lesson_to, value=value), format='json')
        assert resp.status_code == 201, resp.json()
    first = db_lesson(db_school, d(2031, 3, 5), 1)
    second = db_lesson(db_school, d(2031, 3, 12), 2)
    recalc_group(db_school)
    assert (_pay(first), _pay(second)) == (D('900.00'), D('1500.00'))


@pytest.mark.parametrize('first,second,ok', [
    # Те же уроки, периоды стыкуются (valid_until не включительно) — замена.
    ((None, d(2031, 4, 1)), (d(2031, 4, 1), None), True),
    ((d(2031, 1, 1), d(2031, 4, 1)), (d(2031, 5, 1), d(2031, 6, 1)), True),
    # Пустой период ни с чем не пересекается.
    ((d(2031, 4, 1), d(2031, 4, 1)), (None, None), True),
    # Пересекаются хотя бы на день — запрет.
    ((None, d(2031, 4, 2)), (d(2031, 4, 1), None), False),
    ((None, None), (d(2031, 4, 1), d(2031, 5, 1)), False),
])
@pytest.mark.parametrize('lessons', [(None, None), ('1', '5')])
def test_db_excludes_overlap_by_lessons_and_dates(db_school, first, second, ok, lessons):
    rate = db_personal(db_school, lesson_from=lessons[0], lesson_to=lessons[1],
                       valid_from=first[0], valid_until=first[1])

    def second_term():
        PersonalRateTerm.objects.create(
            rate=rate, teacher_id=rate.teacher_id, group_id=rate.group_id,
            valid_from=second[0], valid_until=second[1],
            lesson_from=None if lessons[0] is None else D(lessons[0]),
            lesson_to=None if lessons[1] is None else D(lessons[1]),
            kind='fixed', value=D('1'))
    if ok:
        second_term()
    else:
        with pytest.raises(IntegrityError), transaction.atomic():
            second_term()


def test_engine_picks_term_by_date_in_full_recalc(db_school):
    """Периоды, заведённые напрямую: каждый урок — по периоду своей даты,
    вне периодов — ставка школы."""
    db_version(d(2031, 1, 1))
    rate = db_personal(db_school, value='900', valid_until=d(2031, 3, 10))
    PersonalRateTerm.objects.create(rate=rate, teacher_id=rate.teacher_id,
                                    group_id=rate.group_id, valid_from=d(2031, 3, 20),
                                    kind='plus_fixed', value=D('50'))
    lessons = [db_lesson(db_school, d(2031, 3, day), i)
               for i, day in enumerate((9, 10, 19, 20), start=1)]
    recalc_group(db_school)
    assert [_pay(x) for x in lessons] == [D('900.00'), D('600.00'), D('600.00'), D('650.00')]


# ---------------------------------------------------------------------------
# Список
# ---------------------------------------------------------------------------

def test_list_row_shape_and_other_teacher_flag(client, db_school, clock):
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school, lesson_from='2', lesson_to='4'),
                          format='json').json()['id']
    rows = client.get(PERSONAL, {'group': db_school['group'].id}).json()['rows']
    row = next(r for r in rows if r['id'] == rate_id)
    assert row['group_of_other_teacher'] is False
    assert row['group_teacher_id'] == db_school['t1'].id
    assert row['is_active'] is True
    assert (row['lesson_from'], row['lesson_to'], row['kind'], row['value']) == (
        '2.0', '4.0', 'fixed', '1000.00')
    assert (row['valid_from'], row['valid_until']) == ('2031-03-01', None)
    assert [t['id'] for t in row['terms']] == [row['term_id']]
    assert {'teacher_name', 'group_name', 'direction_color', 'note', 'created_by_name'} <= set(row)

    # Группу передали другому — ставка остаётся, но помечена.
    Group.objects.filter(id=db_school['group'].id).update(teacher=db_school['t2'])
    row = next(r for r in client.get(PERSONAL).json()['rows'] if r['id'] == rate_id)
    assert row['group_of_other_teacher'] is True
    assert row['group_teacher_id'] == db_school['t2'].id


def test_list_query_count_does_not_grow(client, db_school, clock,
                                        django_assert_max_num_queries):
    clock(d(2031, 3, 1))
    groups = [Group.objects.create(
        name=f'Тест Прошлое Группа {i}', direction=db_school['direction'],
        teacher=db_school['t1'], is_individual=False, lesson_duration_minutes=90,
        active=True, created_at=timezone.now()) for i in range(6)]
    for group in groups:
        db_personal(db_school, group=group, valid_until=d(2031, 2, 1))
        db_personal(db_school, group=group, valid_from=d(2031, 2, 1))
    # Сессия/аккаунт + count + страница + периоды — независимо от числа строк.
    with django_assert_max_num_queries(6):
        body = client.get(PERSONAL, {'teacher': db_school['t1'].id}).json()
    assert body['total'] >= 12


# ---------------------------------------------------------------------------
# Ставки школы
# ---------------------------------------------------------------------------

def _version_body(effective_from):
    return {'effective_from': effective_from, 'group_rate': '600', 'individual_rate': '800',
            'cancel_mode': 'percent', 'cancel_value': '50'}


def test_version_in_past_rejected_today_allowed(client, db_school, clock):
    clock(d(2031, 3, 15))
    resp = client.post(VERSIONS, _version_body('2031-03-14'), format='json')
    assert resp.status_code == 400
    assert resp.json()['details']['effective_from'] == [motivation.VERSION_IN_PAST]
    assert client.post(VERSIONS, _version_body('2031-03-15'), format='json').status_code == 201


@pytest.mark.parametrize('start', [d(2031, 1, 1), d(2031, 3, 14)])
def test_version_from_past_date_cannot_be_edited_or_deleted(client, db_school, clock, start):
    version = db_version(start)
    clock(d(2031, 3, 15))

    patch = client.patch(f'{VERSIONS}/{version.id}', {'group_rate': '700'}, format='json')
    delete = client.delete(f'{VERSIONS}/{version.id}')

    for resp in (patch, delete):
        assert resp.status_code == 400
        assert resp.json()['details']['non_field_errors'] == [motivation.VERSION_STARTED]
    version.refresh_from_db()
    assert version.group_rate == D('600.00')


def test_version_started_today_editable_and_deletable_all_day(client, db_school, clock):
    """Начатую сегодня версию правят и удаляют весь день (решение координатора):
    прошлых дат у неё ещё нет — как у периода персональной ставки, начатого сегодня."""
    db_version(d(2031, 1, 1), group_rate=D('500'))
    clock(d(2031, 3, 15))
    version_id = client.post(VERSIONS, _version_body('2031-03-15'), format='json').json()['id']
    lesson = db_lesson(db_school, d(2031, 3, 15), 1)
    recalc_group(db_school)
    assert _pay(lesson) == D('600.00')

    resp = client.patch(f'{VERSIONS}/{version_id}', {'group_rate': '650'}, format='json')
    assert resp.status_code == 200, resp.json()
    assert _pay(lesson) == D('650.00')

    resp = client.delete(f'{VERSIONS}/{version_id}')
    assert resp.status_code == 200, resp.json()
    assert _pay(lesson) == D('500.00')


def test_future_version_editable_but_not_into_past(client, db_school, clock):
    clock(d(2031, 3, 15))
    version = db_version(d(2031, 4, 1))
    lesson = db_lesson(db_school, d(2031, 4, 5), 1)
    recalc_group(db_school)

    resp = client.patch(f'{VERSIONS}/{version.id}', {'group_rate': '700'}, format='json')
    assert resp.status_code == 200, resp.json()
    assert _pay(lesson) == D('700.00')

    resp = client.patch(f'{VERSIONS}/{version.id}', {'effective_from': '2031-03-14'},
                        format='json')
    assert resp.status_code == 400
    assert resp.json()['details']['effective_from'] == [motivation.VERSION_IN_PAST]
    # Перенос на сегодня — можно (версия начнёт действовать сегодня).
    resp = client.patch(f'{VERSIONS}/{version.id}', {'effective_from': '2031-03-15'},
                        format='json')
    assert resp.status_code == 200, resp.json()

    future = db_version(d(2031, 5, 1))
    assert client.delete(f'{VERSIONS}/{future.id}').status_code == 200


def test_versions_editable_flag(client, db_school, clock):
    clock(d(2031, 3, 15))
    past, today, future = db_version(d(2031, 1, 1)), db_version(d(2031, 3, 15)), db_version(
        d(2031, 4, 1))
    flags = {v['id']: v['editable'] for v in client.get(VERSIONS).json()['results']}
    assert flags == {past.id: False, today.id: True, future.id: True}


def test_new_version_recalculates_from_today_only(client, db_school, clock):
    """Новая версия с сегодня: прошлые уроки — по прежней версии и после полного пересчёта."""
    db_version(d(2031, 1, 1), group_rate=D('500'))
    past = db_lesson(db_school, d(2031, 3, 10), 1)
    future = db_lesson(db_school, d(2031, 3, 20), 2)
    recalc_group(db_school)
    clock(d(2031, 3, 15))

    resp = client.post(VERSIONS, {**_version_body('2031-03-15'), 'group_rate': '700'},
                       format='json')

    assert resp.json()['recalculated'] == 1
    _full_recalcs(db_school)
    assert (_pay(past), _pay(future)) == (D('500.00'), D('700.00'))


# ---------------------------------------------------------------------------
# Журнал изменений: ставки не откатываются (решение координатора 2026-10-22)
# ---------------------------------------------------------------------------

def _last_context(model_name: str):
    from django.apps import apps
    event = apps.get_model('payroll', f'{model_name}Event').objects.order_by('-pgh_id').first()
    return event.pgh_context_id


def test_personal_rate_update_not_revertable(client, admin_client, db_school, clock):
    """Откат вернул бы прошлые параметры задним числом мимо §8 и мимо прав
    superadmin — журнал показывает операцию, но откатить её нельзя."""
    clock(d(2031, 3, 1))
    rate_id = client.post(PERSONAL, _body(db_school), format='json').json()['id']
    clock(d(2031, 3, 15))
    client.patch(f'{PERSONAL}/{rate_id}', {'value': '1500'}, format='json')
    context_id = _last_context('PersonalRateTerm')
    terms = _terms(rate_id)

    # Детали операции открывает только superadmin (client — superadmin).
    detail = client.get(f'/api/admin/changelog/{context_id}').json()
    assert detail['operation'] == 'payroll.personal_rate_update'
    assert detail['revertable'] is False
    assert 'периоды персональных ставок' in detail['not_revertable_reason']
    rows = admin_client.get('/api/admin/changelog', {'page_size': 50}).json()['rows']
    row = next(r for r in rows if r['id'] == str(context_id))
    assert row['revertable'] is False

    resp = client.post(f'/api/admin/changelog/{context_id}/revert')  # superadmin: ставки не откатывает никто

    assert resp.status_code == 400
    assert 'периоды персональных ставок' in resp.json()['error']
    assert _terms(rate_id) == terms


def test_rate_version_create_not_revertable(client, admin_client, db_school, clock):
    from apps.payroll.models import PayRateVersion
    clock(d(2031, 3, 15))
    version_id = client.post(VERSIONS, _version_body('2031-04-01'), format='json').json()['id']
    context_id = _last_context('PayRateVersion')

    resp = client.post(f'/api/admin/changelog/{context_id}/revert')  # superadmin: ставки не откатывает никто

    assert resp.status_code == 400
    assert 'ставки школы' in resp.json()['error']
    assert PayRateVersion.objects.filter(id=version_id).exists()


# ---------------------------------------------------------------------------
# Миграция 0008: перенос параметров ставок в периоды
# ---------------------------------------------------------------------------

def test_migration_0008_copies_each_rate_into_one_open_term(db_school):
    """Data-шаг миграции (INSERT … SELECT) на старой схеме: каждая ставка
    получает ровно один период «с начала, бессрочно» с теми же параметрами.

    Приём проекта для data-миграций — вызвать шаг из модуля миграции на текущей
    БД (apps/payments/tests/test_payments_constraints.py). Шаг читает колонки,
    которых в схеме уже нет, поэтому старую схему эмулируем: колонки параметров
    добавляются к таблице ставок внутри транзакции теста (DDL в PostgreSQL
    транзакционный — откатится вместе с тестом). MigrationExecutor назад не
    годится: миграция необратима, а общую тестовую БД откатывать нельзя."""
    import importlib

    from django.db import connection
    from django.db import migrations as dj_migrations

    module = importlib.import_module('apps.payroll.migrations.0008_personal_rate_terms')
    copy_sql = next(op.sql for op in module.Migration.operations
                    if isinstance(op, dj_migrations.RunSQL))

    # ALTER — до вставок: с отложенными FK-событиями строк PG таблицу не меняет.
    with connection.cursor() as cur:
        cur.execute('ALTER TABLE payroll_personal_rates '
                    'ADD COLUMN lesson_from numeric(5,1), ADD COLUMN lesson_to numeric(5,1), '
                    'ADD COLUMN kind text, ADD COLUMN value numeric(10,2)')
    all_lessons = PersonalRate.objects.create(teacher=db_school['t1'], group=db_school['group'])
    ranged = PersonalRate.objects.create(teacher=db_school['t2'], group=db_school['group'])
    ids = [all_lessons.id, ranged.id]
    with connection.cursor() as cur:
        cur.execute("UPDATE payroll_personal_rates SET kind = 'fixed', value = 900 "
                    'WHERE id = %s', [all_lessons.id])
        cur.execute("UPDATE payroll_personal_rates SET lesson_from = 10, lesson_to = 15.5, "
                    "kind = 'plus_percent', value = 12.5 WHERE id = %s", [ranged.id])
        # Шаг копирует все ставки таблицы — чужие строки общей БД убираем
        # (удаление откатится вместе с тестом).
        cur.execute('DELETE FROM payroll_personal_rates WHERE id NOT IN (%s, %s)', ids)
        cur.execute(copy_sql)

    got = {t['rate_id']: t for t in PersonalRateTerm.objects.filter(rate_id__in=ids).values(
        'rate_id', 'teacher_id', 'group_id', 'valid_from', 'valid_until',
        'lesson_from', 'lesson_to', 'kind', 'value')}
    assert PersonalRateTerm.objects.filter(rate_id__in=ids).count() == 2
    assert got[all_lessons.id] == {
        'rate_id': all_lessons.id, 'teacher_id': db_school['t1'].id,
        'group_id': db_school['group'].id, 'valid_from': None, 'valid_until': None,
        'lesson_from': None, 'lesson_to': None, 'kind': 'fixed', 'value': D('900.00')}
    assert got[ranged.id] == {
        'rate_id': ranged.id, 'teacher_id': db_school['t2'].id,
        'group_id': db_school['group'].id, 'valid_from': None, 'valid_until': None,
        'lesson_from': D('10.0'), 'lesson_to': D('15.5'), 'kind': 'plus_percent',
        'value': D('12.50')}
