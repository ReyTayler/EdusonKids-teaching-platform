"""API графика работы: RBAC, форма ответов, валидация, скоуп преподавателя."""
import datetime as dt

import pytest

from apps.availability import services
from apps.availability.models import AvailabilityOverride, AvailabilityRule

pytestmark = pytest.mark.django_db
T = dt.time
FULL_WEEK = {str(i): [] for i in range(7)}


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(services, '_today', lambda: dt.date(2030, 1, 1))


def _admin_base(teacher_id):
    return f'/api/admin/teachers/{teacher_id}/work-schedule'


class TestAuth:
    def test_anon_401(self, anon_client):
        assert anon_client.get('/api/work-schedule/template').status_code == 401

    def test_anon_admin_401(self, anon_client, teacher):
        assert anon_client.get(_admin_base(teacher.id) + '/template').status_code == 401

    def test_anon_mutations_401(self, anon_client, teacher):
        assert anon_client.put('/api/work-schedule/template', {'days': FULL_WEEK},
                               format='json').status_code == 401
        assert anon_client.delete(_admin_base(teacher.id) + '/overrides/2030-01-07').status_code == 401

    def test_manager_cannot_use_teacher_endpoint(self, manager_client):
        assert manager_client.get('/api/work-schedule/template').status_code == 403

    def test_teacher_cannot_use_admin_endpoint(self, own_teacher_client, teacher):
        assert own_teacher_client.get(_admin_base(teacher.id) + '/template').status_code == 403

    @pytest.mark.parametrize('fixture', ['manager_client', 'admin_client', 'superadmin_client'])
    def test_staff_can_edit(self, request, fixture, teacher):
        client = request.getfixturevalue(fixture)
        r = client.put(_admin_base(teacher.id) + '/template',
                       {'days': {**FULL_WEEK, '1': [['10:00', '14:00']]}}, format='json')
        assert r.status_code == 200, r.content
        assert AvailabilityRule.objects.filter(teacher=teacher).count() == 1
        assert r.json()['days']['1'] == [['10:00', '14:00']]


def test_teacher_edits_only_own_schedule(own_teacher_client, teacher, other_teacher):
    r = own_teacher_client.put('/api/work-schedule/template',
                               {'days': {**FULL_WEEK, '3': [['15:00', '20:00']]}}, format='json')
    assert r.status_code == 200
    assert AvailabilityRule.objects.filter(teacher=teacher, day_of_week=3).exists()
    assert not AvailabilityRule.objects.filter(teacher=other_teacher).exists()


def test_teacher_reads_only_own_schedule(own_teacher_client, other_teacher):
    AvailabilityRule.objects.create(teacher=other_teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    assert own_teacher_client.get('/api/work-schedule/template').json()['days']['1'] == []


def test_template_get_shape(own_teacher_client, teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7))
    body = own_teacher_client.get('/api/work-schedule/template').json()
    assert body['days']['1'] == [['10:00', '14:00']]
    assert body['days']['0'] == []
    assert set(body['days']) == {str(i) for i in range(7)}
    assert body['upcoming_overrides'] == [{'date': '2030-01-07', 'intervals': []}]


def test_template_accepts_full_grid(own_teacher_client, teacher):
    r = own_teacher_client.put('/api/work-schedule/template',
                               {'days': {**FULL_WEEK, '0': [['08:00', '22:00']]}}, format='json')
    assert r.status_code == 200, r.content
    assert AvailabilityRule.objects.get(teacher=teacher).end_time == T(22)


@pytest.mark.parametrize('bad', [
    [['10:15', '11:00']], [['07:30', '09:00']], [['21:00', '22:30']], [['12:00', '11:00']],
    [['10:00']], [['10:00:00.5', '11:00']], 'nope', [['25:00', '26:00']], [[None, '11:00']],
    [['10:00\n', '11:00']], [['10:00', '11:00\n']],
])
def test_template_validation(own_teacher_client, teacher, bad):
    r = own_teacher_client.put('/api/work-schedule/template', {'days': {**FULL_WEEK, '1': bad}}, format='json')
    assert r.status_code == 400
    assert not AvailabilityRule.objects.filter(teacher=teacher).exists()


@pytest.mark.parametrize('payload', [{'days': {'7': []}}, {'days': []}, {}])
def test_template_bad_days(own_teacher_client, payload):
    assert own_teacher_client.put('/api/work-schedule/template', payload, format='json').status_code == 400


def test_template_validation_error_readable(own_teacher_client):
    # Фронт (lib/api.ts extractErrorDetail) достаёт текст из details.non_field_errors;
    # под ключом поля 'days' пользователь увидел бы лишь «Validation failed».
    r = own_teacher_client.put('/api/work-schedule/template',
                               {'days': {**FULL_WEEK, '1': [['12:00', '11:00']]}}, format='json')
    assert r.status_code == 400
    assert 'раньше конца' in r.json()['details']['non_field_errors'][0]


def test_validation_error_quotes_input_truncated(own_teacher_client):
    long_value = 'x' * 500
    r = own_teacher_client.put('/api/work-schedule/template',
                               {'days': {**FULL_WEEK, '1': [[long_value, '11:00']]}}, format='json')
    assert r.status_code == 400
    msg = r.json()['details']['non_field_errors'][0]
    assert 'x' * 20 in msg
    assert 'x' * 21 not in msg

    r = own_teacher_client.put('/api/work-schedule/template', {'days': {long_value: []}}, format='json')
    msg = r.json()['details']['non_field_errors'][0]
    assert 'x' * 21 not in msg


def test_validation_error_non_string_not_echoed(own_teacher_client):
    r = own_teacher_client.put('/api/work-schedule/template',
                               {'days': {**FULL_WEEK, '1': [[{'nested': 'secret'}, '11:00']]}},
                               format='json')
    assert r.status_code == 400
    msg = r.json()['details']['non_field_errors'][0]
    assert 'secret' not in msg and 'HH:MM' in msg


def test_teacher_id_from_request_ignored(own_teacher_client, teacher, other_teacher):
    # Преподаватель берётся только из учётки: teacher_id в теле и в query — мусор.
    AvailabilityRule.objects.create(teacher=other_teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    r = own_teacher_client.put(f'/api/work-schedule/template?teacher_id={other_teacher.id}',
                               {'teacher_id': other_teacher.id,
                                'days': {**FULL_WEEK, '3': [['15:00', '20:00']]}}, format='json')
    assert r.status_code == 200, r.content
    assert AvailabilityRule.objects.filter(teacher=teacher, day_of_week=3).exists()
    assert list(AvailabilityRule.objects.filter(teacher=other_teacher)
                .values_list('day_of_week', flat=True)) == [1]
    body = own_teacher_client.get(f'/api/work-schedule/template?teacher_id={other_teacher.id}').json()
    assert body['days']['1'] == []


def test_teacher_account_without_teacher_403(own_teacher_client):
    # CHECK accounts_teacher_role_check не даёт сохранить role=teacher без
    # teacher_id, поэтому учётку «ломаем» в памяти и аутентифицируем напрямую.
    from rest_framework.test import APIRequestFactory, force_authenticate

    from apps.accounts.models import Account
    from apps.availability.views import TeacherTemplateView
    user = Account.objects.get(email='__avail_t__@test.local')
    user.teacher_id = None
    request = APIRequestFactory().get('/api/work-schedule/template')
    force_authenticate(request, user=user)
    r = TeacherTemplateView.as_view()(request)
    assert r.status_code == 403
    assert r.data == {'error': 'Учётка не привязана к преподавателю.'}


def test_range_get(own_teacher_client, teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 8))
    body = own_teacher_client.get('/api/work-schedule?from=2030-01-07&to=2030-01-13').json()
    assert body['has_schedule'] is True
    assert body['windows']['2030-01-07'] == [['10:00', '14:00']]
    assert body['windows']['2030-01-08'] == []
    assert len(body['windows']) == 7
    assert body['override_dates'] == ['2030-01-08']


def test_range_no_schedule(own_teacher_client):
    body = own_teacher_client.get('/api/work-schedule?from=2030-01-07&to=2030-01-07').json()
    assert body['has_schedule'] is False
    assert body['windows'] == {'2030-01-07': []}


def test_range_requires_params(own_teacher_client):
    assert own_teacher_client.get('/api/work-schedule').status_code == 400


def test_week_put_and_override_delete(own_teacher_client, teacher):
    r = own_teacher_client.put('/api/work-schedule/week', {
        'monday': '2030-01-07', 'days': {'2030-01-07': [['16:00', '18:00']]},
    }, format='json')
    assert r.status_code == 200, r.content
    ov = AvailabilityOverride.objects.get(teacher=teacher, date=dt.date(2030, 1, 7))
    assert (ov.start_time, ov.end_time) == (T(16), T(18))
    r = own_teacher_client.delete('/api/work-schedule/overrides/2030-01-07')
    assert r.status_code == 204
    assert not AvailabilityOverride.objects.filter(teacher=teacher).exists()


def test_week_past_date_400(own_teacher_client, monkeypatch):
    monkeypatch.setattr(services, '_today', lambda: dt.date(2030, 1, 9))
    r = own_teacher_client.put('/api/work-schedule/week',
                               {'monday': '2030-01-07', 'days': {'2030-01-07': []}}, format='json')
    assert r.status_code == 400
    assert 'прошла' in r.json()['error']


@pytest.mark.parametrize('payload', [
    {'monday': '2030-01-07', 'days': {'not-a-date': []}},
    {'monday': 'nope', 'days': {}},
    {'monday': '2030-01-07', 'days': {'2030-01-07': [['10:00', '10:00']]}},
])
def test_week_bad_input_400(own_teacher_client, payload):
    assert own_teacher_client.put('/api/work-schedule/week', payload, format='json').status_code == 400


@pytest.mark.parametrize('payload, text', [
    ({'monday': '2030-01-07', 'days': {'2030-01-14': []}}, 'вне недели'),
    ({'monday': '2030-01-08', 'days': {'2030-01-08': []}}, 'понедельник'),
])
def test_week_domain_rules_400(own_teacher_client, teacher, payload, text):
    r = own_teacher_client.put('/api/work-schedule/week', payload, format='json')
    assert r.status_code == 400
    assert text in r.json()['error']
    assert not AvailabilityOverride.objects.filter(teacher=teacher).exists()


def test_override_delete_bad_date_400(own_teacher_client):
    assert own_teacher_client.delete('/api/work-schedule/overrides/not-a-date').status_code == 400


def test_admin_week_targets_path_teacher(admin_client, teacher, other_teacher):
    # Вторник открыт по шаблону — «закрыт» на 08.01 отличается и даёт исключение.
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=2, start_time=T(10), end_time=T(14))
    r = admin_client.put(_admin_base(teacher.id) + '/week',
                         {'monday': '2030-01-07', 'days': {'2030-01-08': []}}, format='json')
    assert r.status_code == 200, r.content
    assert AvailabilityOverride.objects.filter(teacher=teacher, date=dt.date(2030, 1, 8)).exists()
    assert not AvailabilityOverride.objects.filter(teacher=other_teacher).exists()


def test_admin_range_get(admin_client, teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    body = admin_client.get(_admin_base(teacher.id) + '?from=2030-01-07&to=2030-01-07').json()
    assert body['windows'] == {'2030-01-07': [['10:00', '14:00']]}


def test_admin_override_delete(admin_client, teacher):
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7))
    assert admin_client.delete(_admin_base(teacher.id) + '/overrides/2030-01-07').status_code == 204
    assert not AvailabilityOverride.objects.filter(teacher=teacher).exists()


def test_admin_unknown_teacher_404(admin_client):
    r = admin_client.get(_admin_base(2_000_000_000) + '/template')
    assert r.status_code == 404
    assert r.json() == {'error': 'Преподаватель не найден.'}


def test_admin_out_of_int4_teacher_404(admin_client):
    assert admin_client.get(_admin_base(99_999_999_999) + '/template').status_code == 404


def test_admin_unknown_teacher_put_404(admin_client):
    r = admin_client.put(_admin_base(2_000_000_000) + '/template', {'days': FULL_WEEK}, format='json')
    assert r.status_code == 404
    assert r.json()['error'] == 'Преподаватель не найден.'


def test_service_teacher_does_not_exist_maps_to_404(admin_client, teacher, monkeypatch):
    # Проверяет маппинг _mutate: Teacher.DoesNotExist из сервиса → 404 (так
    # проявится удаление преподавателя между проверкой скоупа и блокировкой
    # строки). Саму гонку тест не воспроизводит.
    def gone(*args, **kwargs):
        raise services.Teacher.DoesNotExist
    monkeypatch.setattr(services, 'replace_template', gone)
    r = admin_client.put(_admin_base(teacher.id) + '/template', {'days': FULL_WEEK}, format='json')
    assert r.status_code == 404
    assert r.json()['error'] == 'Преподаватель не найден.'


def test_archived_teacher_read_only(admin_client, teacher):
    teacher.active = False
    teacher.save(update_fields=['active'])
    assert admin_client.get(_admin_base(teacher.id) + '/template').status_code == 200
    r = admin_client.put(_admin_base(teacher.id) + '/template', {'days': FULL_WEEK}, format='json')
    assert r.status_code == 400
    assert 'архиве' in r.json()['error']
    r = admin_client.put(_admin_base(teacher.id) + '/week',
                         {'monday': '2030-01-07', 'days': {'2030-01-08': []}}, format='json')
    assert r.status_code == 400
    assert 'архиве' in r.json()['error']
    r = admin_client.delete(_admin_base(teacher.id) + '/overrides/2030-01-08')
    assert r.status_code == 400
    assert 'архиве' in r.json()['error']


def test_every_view_declares_permissions():
    # DRF default = AllowAny: каждая APIView модуля обязана задать права явно,
    # и ни одна не должна остаться открытой.
    from rest_framework.permissions import AllowAny
    from rest_framework.views import APIView

    from apps.availability import views
    found = [obj for obj in vars(views).values()
             if isinstance(obj, type) and issubclass(obj, APIView) and obj is not APIView]
    assert len(found) == 8
    for cls in found:
        assert 'permission_classes' in vars(cls), cls.__name__
        assert cls.permission_classes and AllowAny not in cls.permission_classes, cls.__name__


def test_changelog_labels():
    from apps.changelog.labels import resolve_operation
    cases = [
        ('PUT', '/api/admin/teachers/5/work-schedule/template', 'schedule.template'),
        ('PUT', '/api/admin/teachers/5/work-schedule/week', 'schedule.week'),
        ('DELETE', '/api/admin/teachers/5/work-schedule/overrides/2030-01-07', 'schedule.override_clear'),
        ('PUT', '/api/work-schedule/template', 'schedule.template'),
        ('PUT', '/api/work-schedule/week', 'schedule.week'),
        ('DELETE', '/api/work-schedule/overrides/2030-01-07', 'schedule.override_clear'),
    ]
    for method, url, expected in cases:
        assert resolve_operation(method, url) == expected, url
