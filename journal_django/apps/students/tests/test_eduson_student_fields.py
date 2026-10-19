"""
Eduson: ученик — имя без уникальности, контакты ребёнка, crm_link (спека §4.2).

У Eduson записано только имя ребёнка («Миша»), имена повторяются — тёзки
различаются по id. Eduson шлёт напоминания и самому ребёнку, поэтому у ученика
свои phone/telegram/email и parent1_telegram. Ссылка в CRM ведёт в amoCRM —
поле нейтральное: crm_link (было bitrix24_link).

Очистка — транзакция pytest.mark.django_db (откат после теста), как у соседей.
"""
from __future__ import annotations

import pytest
from django.utils import timezone

from apps.students.models import Student

pytestmark = pytest.mark.django_db

BASE_URL = '/api/admin/students'
CHILD_FIELDS = {
    'phone': '70000000001',
    'telegram': 'misha',
    'email': 'm@example.com',
    'parent1_telegram': 'mama',
    'crm_link': 'https://example.amocrm.ru/leads/detail/1',
}


def _student(**kw):
    return Student.objects.create(full_name=kw.pop('full_name', 'Миша'), created_at=timezone.now(), **kw)


def test_full_name_not_unique():
    _student()
    _student()  # тёзка — разрешено
    assert Student.objects.filter(full_name='Миша').count() == 2


def test_child_contacts_and_crm_link():
    s = _student(phone='70000000001', telegram='misha', email='m@example.com',
                 parent1_telegram='mama', crm_link='https://example.amocrm.ru/leads/detail/1')
    s.refresh_from_db()
    assert (s.phone, s.telegram, s.email, s.parent1_telegram) == ('70000000001', 'misha', 'm@example.com', 'mama')
    assert s.crm_link.endswith('/1')


def test_bitrix24_link_gone_from_model():
    field_names = {f.name for f in Student._meta.get_fields()}
    assert 'bitrix24_link' not in field_names


def test_api_allows_duplicate_name(admin_client):
    payload = {'full_name': 'Миша'}
    r1 = admin_client.post(BASE_URL, payload, format='json')
    r2 = admin_client.post(BASE_URL, payload, format='json')
    assert r1.status_code == 201, r1.content
    assert r2.status_code == 201, r2.content
    # Тёзки — два разных ученика.
    assert r1.json()['id'] != r2.json()['id']
    assert r1.json()['full_name'] == r2.json()['full_name'] == 'Миша'


def test_api_patch_to_existing_name_allowed(admin_client):
    a = _student(full_name='Миша')
    b = _student(full_name='Петя')
    resp = admin_client.patch(f'{BASE_URL}/{b.id}', {'full_name': 'Миша'}, format='json')
    assert resp.status_code == 200, resp.content
    assert Student.objects.filter(full_name='Миша').count() == 2
    assert a.id != b.id


def test_api_create_writes_and_returns_child_fields(admin_client):
    resp = admin_client.post(BASE_URL, {'full_name': 'Миша', **CHILD_FIELDS}, format='json')
    assert resp.status_code == 201, resp.content
    body = resp.json()
    for key, value in CHILD_FIELDS.items():
        assert body[key] == value, key
    assert 'bitrix24_link' not in body

    stored = Student.objects.get(id=body['id'])
    for key, value in CHILD_FIELDS.items():
        assert getattr(stored, key) == value, key


def test_api_get_returns_child_fields(admin_client):
    s = _student(**CHILD_FIELDS)
    body = admin_client.get(f'{BASE_URL}/{s.id}').json()
    for key, value in CHILD_FIELDS.items():
        assert body[key] == value, key

    # Список отдаёт тот же контракт строки.
    listed = admin_client.get(f'{BASE_URL}?page_size=500&sort_by=id&sort_dir=desc').json()
    row = next(r for r in listed['rows'] if r['id'] == s.id)
    for key, value in CHILD_FIELDS.items():
        assert row[key] == value, key


def test_api_patch_updates_child_fields(admin_client):
    s = _student()
    resp = admin_client.patch(f'{BASE_URL}/{s.id}', CHILD_FIELDS, format='json')
    assert resp.status_code == 200, resp.content
    s.refresh_from_db()
    for key, value in CHILD_FIELDS.items():
        assert getattr(s, key) == value, key


@pytest.mark.parametrize('field, bad', [
    ('email', 'not-an-email'),
    ('crm_link', 'example.amocrm.ru/leads/1'),  # без схемы — не ссылка
])
def test_api_rejects_invalid_child_fields(admin_client, field, bad):
    resp = admin_client.post(BASE_URL, {'full_name': 'Миша', field: bad}, format='json')
    assert resp.status_code == 400
    assert field in resp.json()['details']


def test_api_legacy_bitrix24_link_not_stored(admin_client):
    """Старое имя поля больше не часть контракта: не сохраняется в crm_link."""
    resp = admin_client.post(
        BASE_URL,
        {'full_name': 'Миша', 'bitrix24_link': 'https://b24.example/crm/1'},
        format='json',
    )
    assert resp.status_code == 201
    assert resp.json()['crm_link'] is None


@pytest.mark.parametrize('empty', [None, ''])
def test_api_patch_clears_nullable_fields(admin_client, empty):
    """PATCH с null/'' очищает поле (раньше пустое значение молча игнорировалось)."""
    s = _student(**CHILD_FIELDS, parent2_phone='79990000000')
    resp = admin_client.patch(
        f'{BASE_URL}/{s.id}',
        {'phone': empty, 'crm_link': empty, 'parent2_phone': empty},
        format='json',
    )
    assert resp.status_code == 200, resp.content
    s.refresh_from_db()
    assert (s.phone, s.crm_link, s.parent2_phone) == (None, None, None)
    # Неприсланные поля не тронуты, имя на месте.
    assert s.telegram == CHILD_FIELDS['telegram']
    assert s.full_name == 'Миша'
