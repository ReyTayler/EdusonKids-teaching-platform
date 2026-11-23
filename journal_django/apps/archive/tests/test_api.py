"""Archive API: global search, pagination, and superadmin-only access."""
import uuid

import pytest
from django.utils import timezone

from apps.directions.models import Direction
from apps.groups.models import Group
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db
URL = '/api/admin/archive'


@pytest.fixture
def archived():
    prefix = 'ArchiveQA-' + uuid.uuid4().hex[:12]
    teacher = Teacher.objects.create(name=f'{prefix} Преподаватель', email=f'{prefix}@example.invalid', phone='+79990001234', active=False, created_at=timezone.now())
    direction = Direction.objects.create(name=f'{prefix} Направление', active=False)
    group = Group.objects.create(name=f'{prefix} Группа', teacher=teacher, direction=direction, is_individual=True, active=False, created_at=timezone.now())
    active = Teacher.objects.create(name=f'{prefix} Активный', active=True, created_at=timezone.now())
    return prefix, teacher, direction, group, active


@pytest.mark.parametrize('client_name,expected', [('anon_client', 401), ('teacher_client', 403), ('manager_client', 403), ('admin_client', 403)])
def test_access(request, client_name, expected):
    assert request.getfixturevalue(client_name).get(URL).status_code == expected


def test_search_all_types_and_exclude_active(superadmin_client, archived):
    prefix, teacher, direction, group, active = archived
    response = superadmin_client.get(URL, {'q': prefix})
    assert response.status_code == 200, response.data
    data = response.json()
    assert data['total'] == 3
    assert data['counts'] == {'teacher': 1, 'group': 1, 'direction': 1}
    assert {(r['entity_type'], r['id']) for r in data['rows']} == {('teacher', teacher.id), ('direction', direction.id), ('group', group.id)}
    assert all(set(row) == {'id', 'name', 'entity_type', 'description'} for row in data['rows'])


def test_contact_related_fields_and_multiword_search(superadmin_client, archived):
    prefix, teacher, direction, group, _ = archived
    email = superadmin_client.get(URL, {'q': teacher.email}).json()
    assert email['counts']['teacher'] == 1
    phone = superadmin_client.get(URL, {'q': '79990001234'}).json()
    assert any(r['id'] == teacher.id and r['entity_type'] == 'teacher' for r in phone['rows'])
    related = superadmin_client.get(URL, {'q': f'{prefix} Преподаватель Направление'}).json()
    assert related['total'] == 1
    assert related['rows'][0]['id'] == group.id
    empty = superadmin_client.get(URL, {'q': prefix + ' отсутствует'}).json()
    assert empty['total'] == 0


def test_facet_preserves_global_counts(superadmin_client, archived):
    prefix, _, _, group, _ = archived
    data = superadmin_client.get(URL, {'q': prefix, 'entity_type': 'group'}).json()
    assert data['total'] == 1
    assert data['rows'][0]['id'] == group.id
    assert data['counts'] == {'teacher': 1, 'group': 1, 'direction': 1}
    assert all(data['archive_counts'][key] >= data['counts'][key] for key in data['counts'])


def test_id_search_and_large_numbers(superadmin_client, archived):
    _, _, _, group, _ = archived
    data = superadmin_client.get(URL, {'q': f'#{group.id}'}).json()
    assert ('group', group.id) in {(r['entity_type'], r['id']) for r in data['rows']}
    assert superadmin_client.get(URL, {'q': '99999999999999999999999'}).status_code == 200


def test_pagination_and_search_beyond_first_500(superadmin_client, archived):
    prefix, teacher, direction, _, _ = archived
    rows = [Group(name=f'{prefix}-bulk-{i:03}', teacher=teacher, direction=direction, is_individual=True, active=False, created_at=timezone.now()) for i in range(601)]
    Group.objects.bulk_create(rows)
    query = f'{prefix}-bulk-'
    first = superadmin_client.get(URL, {'q': query, 'page_size': 50}).json()
    second = superadmin_client.get(URL, {'q': query, 'page_size': 50, 'page': 2}).json()
    assert first['total'] == 601
    assert len(first['rows']) == len(second['rows']) == 50
    assert not ({r['id'] for r in first['rows']} & {r['id'] for r in second['rows']})
    last = superadmin_client.get(URL, {'q': f'{prefix}-bulk-600'}).json()
    assert last['total'] == 1
    assert last['rows'][0]['name'].endswith('600')


@pytest.mark.parametrize('params', [{'entity_type': 'invalid'}, {'q': 'a' * 201}])
def test_validate_query(superadmin_client, params):
    assert superadmin_client.get(URL, params).status_code == 400
