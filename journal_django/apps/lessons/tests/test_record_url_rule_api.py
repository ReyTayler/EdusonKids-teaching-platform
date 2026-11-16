"""GET/PUT /api/admin/lessons/record-url-rule — правило ссылки на запись урока
(спека 2026-11-16). Читают manager/admin/superadmin, меняет только superadmin:
страница «Настройки» видна только ему."""
from __future__ import annotations

import pytest

from apps.core.tests.minimal_school import ensure_minimal_school
from apps.lessons.models import RecordUrlRule, RecordUrlRuleEvent
from apps.lessons.record_url_rule import load_rule

pytestmark = pytest.mark.django_db

URL = '/api/admin/lessons/record-url-rule'


def _put(client, **body):
    payload = {'required': True, 'teacher_ids': [], 'group_ids': []}
    payload.update(body)
    return client.put(URL, payload, format='json')


# ---------------------------------------------------------------------------
# Права
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('client_fixture', ['manager_client', 'admin_client',
                                            'superadmin_client'])
def test_get_default_for_staff(request, client_fixture):
    r = request.getfixturevalue(client_fixture).get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body['required'] is True
    assert body['teachers'] == [] and body['groups'] == []


def test_teacher_forbidden(teacher_client):
    assert teacher_client.get(URL).status_code == 403
    assert _put(teacher_client, required=False).status_code == 403


def test_anon_unauthorized(anon_client):
    assert anon_client.get(URL).status_code in (401, 403)


@pytest.mark.parametrize('client_fixture', ['manager_client', 'admin_client'])
def test_only_superadmin_can_put(request, client_fixture):
    r = _put(request.getfixturevalue(client_fixture), required=False)
    assert r.status_code == 403
    assert load_rule().required is True


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------

def test_put_replaces_and_returns_names(superadmin_client):
    ids = ensure_minimal_school()
    r = _put(superadmin_client, required=False, teacher_ids=[ids['teacher_id']],
             group_ids=[ids['group_id'], ids['group_id']])
    assert r.status_code == 200
    body = r.json()
    assert body['required'] is False
    assert [t['id'] for t in body['teachers']] == [ids['teacher_id']]
    assert [g['id'] for g in body['groups']] == [ids['group_id']]   # дубль схлопнут
    assert all(t['name'] for t in body['teachers'] + body['groups'])
    assert body['updated_by'] == '__root_superadmin__@test.local'

    # Правило реально сохранено, а не только отражено в ответе.
    snap = load_rule()
    assert snap.required is False
    assert snap.teacher_ids == frozenset({ids['teacher_id']})
    assert snap.group_ids == frozenset({ids['group_id']})
    assert superadmin_client.get(URL).json() == body


def test_put_replaces_whole_set(superadmin_client):
    """Второй PUT заменяет наборы целиком: пустой список снимает все исключения."""
    ids = ensure_minimal_school()
    _put(superadmin_client, teacher_ids=[ids['teacher_id']], group_ids=[ids['group_id']])
    r = _put(superadmin_client, teacher_ids=[], group_ids=[ids['group_id']])
    assert r.status_code == 200
    assert r.json()['teachers'] == []
    assert load_rule().teacher_ids == frozenset()
    assert load_rule().group_ids == frozenset({ids['group_id']})


def test_unknown_ids_400_and_nothing_saved(superadmin_client):
    ids = ensure_minimal_school()
    r = _put(superadmin_client, required=False, teacher_ids=[999999999],
             group_ids=[ids['group_id']])
    assert r.status_code == 400
    details = r.json()['details']
    assert 'teacher_ids' in details and '999999999' in str(details['teacher_ids'])
    snap = load_rule()
    assert snap.required is True and snap.group_ids == frozenset()


def test_unknown_group_ids_400(superadmin_client):
    r = _put(superadmin_client, group_ids=[999999999])
    assert r.status_code == 400
    assert 'group_ids' in r.json()['details']


@pytest.mark.parametrize('body', [
    {'required': None, 'teacher_ids': [], 'group_ids': []},
    {'teacher_ids': [], 'group_ids': []},
    {'required': True, 'teacher_ids': 'abc', 'group_ids': []},
    {'required': True, 'teacher_ids': [0], 'group_ids': []},
    {'required': True, 'teacher_ids': [], 'group_ids': ['x']},
    {'required': True, 'teacher_ids': []},
])
def test_invalid_body_400(superadmin_client, body):
    r = superadmin_client.put(URL, body, format='json')
    assert r.status_code == 400
    assert load_rule().required is True


def test_repeat_put_writes_no_events(superadmin_client):
    """Повторное сохранение того же содержимого не пишет пустых событий журнала."""
    ids = ensure_minimal_school()
    _put(superadmin_client, required=False, group_ids=[ids['group_id']])
    before = RecordUrlRuleEvent.objects.count()
    r = _put(superadmin_client, required=False, group_ids=[ids['group_id']])
    assert r.status_code == 200
    assert RecordUrlRuleEvent.objects.count() == before


# ---------------------------------------------------------------------------
# Журнал изменений
# ---------------------------------------------------------------------------

def test_operation_label():
    from apps.changelog.labels import resolve_operation
    assert resolve_operation('PUT', URL) == 'lesson.record_url_rule'


def test_changelog_records_put(superadmin_client):
    _put(superadmin_client, required=False)
    context_id = (RecordUrlRuleEvent.objects.filter(pgh_context_id__isnull=False)
                  .order_by('-pgh_id').values_list('pgh_context_id', flat=True).first())
    assert context_id is not None
    detail = superadmin_client.get(f'/api/admin/changelog/{context_id}').json()
    assert detail['operation'] == 'lesson.record_url_rule'
    assert RecordUrlRule.load().required is False


def test_changelog_names_who_was_excepted(superadmin_client):
    """Лента и детали называют преподавателя и группу, а не id строки-связки."""
    ids = ensure_minimal_school()
    _put(superadmin_client, required=False, teacher_ids=[ids['teacher_id']],
         group_ids=[ids['group_id']])
    _put(superadmin_client, required=False, teacher_ids=[], group_ids=[ids['group_id']])

    rows = superadmin_client.get('/api/admin/changelog').json()['rows']
    by_summary = {r['summary']: r for r in rows}
    assert set(by_summary) == {
        'Ссылка на запись урока: необязательна; исключения: '
        '+ преподаватель Тест Преподаватель 1, + группа «Тест Группа 1»',
        'Ссылка на запись урока: исключения: − преподаватель Тест Преподаватель 1',
    }
    removed = by_summary[
        'Ссылка на запись урока: исключения: − преподаватель Тест Преподаватель 1']

    detail = superadmin_client.get(f'/api/admin/changelog/{removed["id"]}').json()
    link = next(e for e in detail['events'] if e['entity'] == 'record_url_rule_teacher')
    assert link['description'] == (
        'Исключение из правила ссылки снято: преподаватель Тест Преподаватель 1')
    assert 'Тест Преподаватель 1' in link['human']['title']


def test_inactive_group_exception_saved_and_returned(superadmin_client):
    """Неактивная группа в исключениях не теряется: PUT сохраняет, GET отдаёт."""
    from apps.groups.models import Group

    ids = ensure_minimal_school()
    Group.objects.filter(id=ids['group_id']).update(active=False)
    r = _put(superadmin_client, required=False, group_ids=[ids['group_id']])
    assert r.status_code == 200, r.content
    assert [g['id'] for g in r.json()['groups']] == [ids['group_id']]
    got = superadmin_client.get(URL).json()
    assert [g['id'] for g in got['groups']] == [ids['group_id']]
    assert load_rule().group_ids == frozenset({ids['group_id']})
