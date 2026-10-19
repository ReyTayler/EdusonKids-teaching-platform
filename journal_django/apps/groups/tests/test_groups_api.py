"""
E2E тесты для /api/admin/groups.

Фаза 4: аутентификация через JWT (access-cookie). Старые HMAC _make_cookie
и sentinel account_id=42 заменены на реальные аккаунты через admin_client /
manager_client / teacher_client из conftest.

Тестируемые кейсы:
  - без cookie → 401
  - cookie role=teacher → 403
  - cookie role=admin → 200 (список)
  - cookie role=manager → 200 (список)
  - GET /?filter[active]=true → 200, все rows active=true
  - GET /?sort_by=invalid → 400
  - GET /:id → 200 с нужными полями
  - GET /999999999 → 404 {error: 'Not found'}
  - POST → 201, группа создана в БД со slots
  - POST невалидное тело → 400
  - PATCH → 200
  - PATCH /999999999 → 404
  - DELETE → 204, группа стала active=false
  - DELETE /999999999 → 404
"""
from __future__ import annotations

import pytest
from django.db import connection
from rest_framework.test import APIClient

from apps.core.tests.minimal_school import ensure_minimal_school

BASE_URL = '/api/admin/groups'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cleanup_group(group_id: int) -> None:
    with connection.cursor() as cur:
        # форк Eduson: тест раньше пропускался из-за пустой БД. FK слотов на группу
        # без ON DELETE CASCADE в БД (каскад у модели — только ORM), поэтому сырой
        # DELETE группы оставлял слоты, и проверка ограничений в конце теста падала.
        cur.execute('DELETE FROM group_schedule_slots WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


def _get_valid_direction_id() -> int:
    return ensure_minimal_school()['direction_id']


def _get_valid_teacher_id() -> int:
    return ensure_minimal_school()['teacher_id']


def _group_payload(**overrides) -> dict:
    return {
        'name': '__test_api_group__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 2,
        'slots': [{'day_of_week': 1, 'start_time': '10:00'}],
        **overrides,
    }


# ---------------------------------------------------------------------------
# Authentication / authorization tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_cookie_returns_401(anon_client):
    resp = anon_client.get(BASE_URL)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_teacher_cookie_returns_403(teacher_client):
    resp = teacher_client.get(BASE_URL)
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_cookie_returns_200(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_manager_cookie_returns_200(manager_client):
    resp = manager_client.get(BASE_URL)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/admin/groups  — list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_response_shape(admin_client):
    """Ответ содержит {rows, total, page, page_size}."""
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    data = resp.json()
    assert 'rows' in data, f"Expected 'rows' key, got: {list(data.keys())}"
    assert 'total' in data
    assert 'page' in data
    assert 'page_size' in data
    assert isinstance(data['rows'], list)
    assert isinstance(data['total'], int)
    assert data['page'] == 1


@pytest.mark.django_db
def test_list_filter_active_true(admin_client):
    resp = admin_client.get(BASE_URL + '?filter[active]=true')
    assert resp.status_code == 200
    for row in resp.json()['rows']:
        assert row['active'] is True


@pytest.mark.django_db
def test_list_filter_active_false(admin_client):
    resp = admin_client.get(BASE_URL + '?filter[active]=false')
    assert resp.status_code == 200
    for row in resp.json()['rows']:
        assert row['active'] is False


@pytest.mark.django_db
def test_list_sort_by_name_asc(admin_client):
    resp = admin_client.get(BASE_URL + '?sort_by=name&sort_dir=asc&page_size=10')
    assert resp.status_code == 200
    assert isinstance(resp.json()['rows'], list)


@pytest.mark.django_db
def test_list_sort_by_lessons_done(admin_client):
    """«Пройдено» — сортируемая колонка списка (whitelist ORDERING_FIELDS).

    На проводе — строка с сохранённым scale ('12.5'), как и все numeric-поля:
    DateSafeJSONRenderer отдаёт Decimal через str(), см. apps.core.renderers.
    """
    for sort_dir in ('asc', 'desc'):
        resp = admin_client.get(
            BASE_URL + f'?sort_by=lessons_done&sort_dir={sort_dir}&page_size=10')
        assert resp.status_code == 200
        for row in resp.json()['rows']:
            assert float(row['lessons_done']) >= 0


@pytest.mark.django_db
def test_list_sort_by_invalid_returns_400(admin_client):
    resp = admin_client.get(BASE_URL + '?sort_by=nonexistent')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_list_sort_dir_invalid_returns_400(admin_client):
    resp = admin_client.get(BASE_URL + '?sort_dir=sideways')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_list_page_size_param(admin_client):
    resp = admin_client.get(BASE_URL + '?page_size=2')
    assert resp.status_code == 200
    data = resp.json()
    assert data['page_size'] == 2
    assert len(data['rows']) <= 2


@pytest.mark.django_db
def test_list_rows_have_slots_field(admin_client):
    resp = admin_client.get(BASE_URL + '?page_size=5')
    assert resp.status_code == 200
    for row in resp.json()['rows']:
        assert 'slots' in row
        assert isinstance(row['slots'], list)


@pytest.mark.django_db
def test_list_rows_have_direction_name(admin_client):
    resp = admin_client.get(BASE_URL + '?page_size=5')
    assert resp.status_code == 200
    rows = resp.json()['rows']
    if rows:
        assert 'direction_name' in rows[0]


@pytest.mark.django_db
def test_list_filter_by_name_no_match(admin_client):
    resp = admin_client.get(BASE_URL + '?filter[name]=__nonexistent_xyz_filter__')
    assert resp.status_code == 200
    assert resp.json()['rows'] == []


# ---------------------------------------------------------------------------
# GET /api/admin/groups/:id — retrieve
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_retrieve_nonexistent_returns_404(admin_client):
    resp = admin_client.get(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_retrieve_existing_returns_200(admin_client):
    from apps.groups import repository
    data = {
        'name': '__test_api_get__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
        'slots': [{'day_of_week': 2, 'start_time': '12:00'}],
    }
    group = repository.create_group(data)
    try:
        resp = admin_client.get(f"{BASE_URL}/{group['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body['id'] == group['id']
        assert body['name'] == '__test_api_get__'
        assert isinstance(body['slots'], list)
        assert len(body['slots']) == 1
        assert body['slots'][0]['day_of_week'] == 2
    finally:
        _cleanup_group(group['id'])


# ---------------------------------------------------------------------------
# POST /api/admin/groups — create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_returns_201(admin_client):
    payload = _group_payload(name='__test_post_201__')
    resp = admin_client.post(BASE_URL, payload, format='json')
    if resp.status_code == 201:
        _cleanup_group(resp.json()['id'])
    assert resp.status_code == 201


@pytest.mark.django_db
def test_create_persists_in_db(admin_client):
    from apps.groups import repository
    payload = _group_payload(name='__test_post_db__')
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 201
    group_id = resp.json()['id']
    try:
        fetched = repository.get_group(group_id)
        assert fetched is not None
        assert fetched['name'] == '__test_post_db__'
    finally:
        _cleanup_group(group_id)


@pytest.mark.django_db
def test_create_persists_slots_in_db(admin_client):
    from apps.groups import repository
    payload = _group_payload(
        name='__test_post_slots__',
        slots=[
            {'day_of_week': 0, 'start_time': '09:00'},
            {'day_of_week': 4, 'start_time': '17:00'},
        ],
    )
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 201
    group_id = resp.json()['id']
    try:
        fetched = repository.get_group(group_id)
        assert len(fetched['slots']) == 2
        days = sorted(s['day_of_week'] for s in fetched['slots'])
        assert days == [0, 4]
    finally:
        _cleanup_group(group_id)


@pytest.mark.django_db
def test_create_missing_name_returns_400(admin_client):
    payload = {
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 2,
    }
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_invalid_lesson_duration_returns_400(admin_client):
    payload = _group_payload(name='__bad_dur__', lesson_duration_minutes=75)
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_slot_invalid_day_returns_400(admin_client):
    payload = _group_payload(
        name='__bad_day__',
        slots=[{'day_of_week': 7, 'start_time': '10:00'}],
    )
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/admin/groups/:id — update
# ---------------------------------------------------------------------------

@pytest.fixture
def existing_group():
    """Создаёт группу и удаляет её после теста."""
    from apps.groups import repository
    data = {
        'name': '__test_patch_group__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
    }
    group = repository.create_group(data)
    yield group
    _cleanup_group(group['id'])


@pytest.mark.django_db
def test_patch_returns_200(admin_client, existing_group):
    resp = admin_client.patch(
        f"{BASE_URL}/{existing_group['id']}",
        {'name': '__test_patch_name_new__'},
        format='json',
    )
    assert resp.status_code == 200


@pytest.mark.django_db
def test_patch_updates_name(admin_client, existing_group):
    resp = admin_client.patch(
        f"{BASE_URL}/{existing_group['id']}",
        {'name': '__test_patch_name_updated__'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['name'] == '__test_patch_name_updated__'


@pytest.mark.django_db
def test_patch_nonexistent_returns_404(admin_client):
    resp = admin_client.patch(
        f'{BASE_URL}/999999999',
        {'name': 'ghost'},
        format='json',
    )
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_patch_active_false(superadmin_client, existing_group):
    """Архивация через PATCH active=false — доступна суперадмину."""
    resp = superadmin_client.patch(
        f"{BASE_URL}/{existing_group['id']}",
        {'active': False},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['active'] is False


@pytest.mark.django_db
def test_patch_active_ignored_for_admin(admin_client, existing_group):
    """Смена active НЕ-суперадмином игнорируется (архивировать/разархивировать
    группу через PATCH может только суперадмин): 200, но active не меняется."""
    resp = admin_client.patch(
        f"{BASE_URL}/{existing_group['id']}",
        {'active': False, 'name': '__test_patch_admin_noactive__'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['active'] is True                       # active не тронут
    assert resp.json()['name'] == '__test_patch_admin_noactive__'  # прочая правка прошла


@pytest.mark.django_db
def test_patch_ignores_slots(admin_client):
    """PATCH группы НЕ трогает расписание: слоты меняются только через
    schedule-change (версионный путь). Форма группы может прислать slots —
    сервер их игнорирует, чтобы не стирать версионную историю (см. Blocker-фикс
    мультислотового аудита)."""
    from apps.groups import repository
    data = {
        'name': '__test_patch_slots__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
        'slots': [{'day_of_week': 0, 'start_time': '08:00'}],
    }
    group = repository.create_group(data)
    try:
        resp = admin_client.patch(
            f"{BASE_URL}/{group['id']}",
            {'slots': [
                {'day_of_week': 5, 'start_time': '11:00'},
                {'day_of_week': 6, 'start_time': '12:00'},
            ]},
            format='json',
        )
        assert resp.status_code == 200
        fetched = repository.get_group(group['id'])
        # Слоты не заменены присланными — остался исходный.
        days = sorted(s['day_of_week'] for s in fetched['slots'])
        assert days == [0]
    finally:
        _cleanup_group(group['id'])


@pytest.mark.django_db
def test_patch_preserves_versioned_slot_history(admin_client):
    """Blocker-регрессия: правка обычного поля (имя) через форму НЕ уничтожает
    версионную историю слотов. Раньше форма присылала активные слоты в PATCH,
    update_group делал delete+recreate без effective_from → вся история и
    будущие/закрытые слоты стирались. Теперь slots на PATCH игнорируются."""
    from apps.groups import repository
    data = {
        'name': '__test_patch_versioned__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
        'slots': [{'day_of_week': 1, 'start_time': '10:00'}],
    }
    group = repository.create_group(data)
    try:
        # Версионная смена расписания: старый слот закрывается, новый открывается.
        repository.apply_schedule_change(
            group['id'], '2026-03-01',
            [{'day_of_week': 3, 'start_time': '19:00'}],
        )
        before = repository.get_schedule(group['id'])['slots']
        assert len(before) == 2  # закрытый исторический + новый активный

        # Правка имени через форму — фронт эхо-передаёт активные слоты.
        resp = admin_client.patch(
            f"{BASE_URL}/{group['id']}",
            {
                'name': '__test_patch_versioned_renamed__',
                'slots': [{'day_of_week': 3, 'start_time': '19:00'}],
            },
            format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['name'] == '__test_patch_versioned_renamed__'

        # История версий не тронута: обе строки на месте с прежними датами.
        after = repository.get_schedule(group['id'])['slots']
        assert len(after) == 2
        closed = [s for s in after if s['effective_to'] is not None]
        assert len(closed) == 1
        assert closed[0]['effective_to'] == '2026-02-28'
    finally:
        _cleanup_group(group['id'])


# ---------------------------------------------------------------------------
# DELETE /api/admin/groups/:id — soft delete
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_delete_returns_204(superadmin_client):
    """Архивация группы (soft-delete) — доступна суперадмину."""
    from apps.groups import repository
    data = {
        'name': '__test_del_204__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
    }
    group = repository.create_group(data)
    try:
        resp = superadmin_client.delete(f"{BASE_URL}/{group['id']}")
        assert resp.status_code == 204
    finally:
        _cleanup_group(group['id'])


@pytest.mark.django_db
def test_delete_sets_active_false(superadmin_client):
    from apps.groups import repository
    data = {
        'name': '__test_del_active__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
    }
    group = repository.create_group(data)
    try:
        resp = superadmin_client.delete(f"{BASE_URL}/{group['id']}")
        assert resp.status_code == 204
        fetched = repository.get_group(group['id'])
        assert fetched['active'] is False
    finally:
        _cleanup_group(group['id'])


@pytest.mark.django_db
def test_delete_nonexistent_returns_404(superadmin_client):
    resp = superadmin_client.delete(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_delete_forbidden_for_admin_and_manager(admin_client, manager_client):
    """Архивировать группу может только суперадмин: admin/manager → 403, группа цела."""
    from apps.groups import repository
    data = {
        'name': '__test_del_forbidden__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
    }
    group = repository.create_group(data)
    try:
        for client in (admin_client, manager_client):
            resp = client.delete(f"{BASE_URL}/{group['id']}")
            assert resp.status_code == 403
        assert repository.get_group(group['id'])['active'] is True  # не тронута
    finally:
        _cleanup_group(group['id'])
