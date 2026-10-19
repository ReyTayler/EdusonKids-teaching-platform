"""
E2E тесты для /api/admin/students.

Используют DRF APIClient с реальной БД (managed=False, продовая).
Все созданные строки удаляются в teardown (прямой DELETE через connection).
Паттерн очистки — зеркало Nest e2e (test/nest/groups.e2e.test.js) after().

Cookie:
  Генерируются через _make_cookie() из conftest.py — идентично Node.js sign().
  ADMIN_COOKIE_SECRET переопределяется через pytest-django фикстуру settings.

Тестируемые кейсы:
  - без cookie → 401
  - cookie role=teacher → 403
  - cookie role=admin → 200 (список)
  - cookie role=manager → 200 (список)
  - Форма list-ответа: {rows, total, page, page_size}
  - Фильтры: full_name (нет совпадений)
  - Сортировка: невалидный sort_by → 400
  - GET /:id → 200 с нужными полями
  - GET /999999999 → 404 {error: 'Not found'}
  - GET /:id/stats → 200 с {student_id, directions, groups, overall}
  - GET /999999999/stats → 404
  - GET /:id/balance → 200 с {paid_by_direction, attended_by_direction, total_balance, total_paid_amount, payments}
  - POST → 201, ученик создан в БД
  - POST без full_name → 400
  - PATCH → 200
  - PATCH /999999999 → 404
  - DELETE → 405 (soft-delete удалён вместе со статусом 'not_enrolled')
"""
from __future__ import annotations

import pytest
from django.db import connection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = '/api/admin/students'


def _cleanup_student(student_id: int) -> None:
    """Прямой DELETE — аналог Nest e2e after() через пул."""
    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


def _student_payload(**overrides) -> dict:
    return {
        'full_name': '__test_api_student__',
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
# GET /api/admin/students — list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_response_shape(admin_client):
    """Ответ содержит {rows, total, page, page_size} — как Express paginate()."""
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
def test_list_filter_full_name_no_match(admin_client):
    resp = admin_client.get(BASE_URL + '?filter[full_name]=__nonexistent_xyz_student__')
    assert resp.status_code == 200
    assert resp.json()['rows'] == []


@pytest.mark.django_db
def test_list_sort_by_invalid_returns_400(admin_client):
    resp = admin_client.get(BASE_URL + '?sort_by=nonexistent_field')
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
def test_list_sort_by_full_name_asc(admin_client):
    resp = admin_client.get(BASE_URL + '?sort_by=full_name&sort_dir=asc&page_size=10')
    assert resp.status_code == 200
    assert isinstance(resp.json()['rows'], list)


@pytest.mark.django_db
def test_list_sort_by_created_at(admin_client):
    resp = admin_client.get(BASE_URL + '?sort_by=created_at&sort_dir=desc&page_size=5')
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/admin/students/:id — retrieve
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_retrieve_nonexistent_returns_404(admin_client):
    """Точный формат 404 — {error: 'Not found'}."""
    resp = admin_client.get(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_retrieve_existing_returns_200(admin_client):
    from apps.students import repository
    student = repository.create_student({'full_name': '__test_api_get__'})
    try:
        resp = admin_client.get(f"{BASE_URL}/{student['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body['id'] == student['id']
        assert body['full_name'] == '__test_api_get__'
        assert 'stage' in body
        assert 'created_at' in body
    finally:
        _cleanup_student(student['id'])


# ---------------------------------------------------------------------------
# GET /api/admin/students/:id/stats
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_stats_nonexistent_returns_404(admin_client):
    resp = admin_client.get(f'{BASE_URL}/999999999/stats')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_stats_existing_returns_200(admin_client):
    from apps.students import repository
    student = repository.create_student({'full_name': '__test_api_stats__'})
    try:
        resp = admin_client.get(f"{BASE_URL}/{student['id']}/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert 'student_id' in body
        assert 'directions' in body
        assert 'groups' in body
        assert 'overall' in body
        assert body['student_id'] == student['id']
        assert isinstance(body['directions'], list)
        assert isinstance(body['groups'], list)
    finally:
        _cleanup_student(student['id'])


@pytest.mark.django_db
def test_stats_overall_shape(admin_client):
    """overall содержит нужные ключи."""
    from apps.students import repository
    student = repository.create_student({'full_name': '__test_api_stats_overall__'})
    try:
        resp = admin_client.get(f"{BASE_URL}/{student['id']}/stats")
        assert resp.status_code == 200
        overall = resp.json()['overall']
        for key in ['lessons_recorded', 'attended_count', 'missed_count',
                    'denominator', 'attendance_pct', 'this_month']:
            assert key in overall
    finally:
        _cleanup_student(student['id'])


# ---------------------------------------------------------------------------
# GET /api/admin/students/:id/balance
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_balance_returns_200_for_new_student(admin_client):
    """Express не проверяет существование — просто возвращает данные."""
    from apps.students import repository
    student = repository.create_student({'full_name': '__test_api_balance__'})
    try:
        resp = admin_client.get(f"{BASE_URL}/{student['id']}/balance")
        assert resp.status_code == 200
        body = resp.json()
        assert 'paid_by_direction' in body
        assert 'attended_by_direction' in body
        assert 'total_balance' in body
        assert 'total_paid_amount' in body
        assert 'payments' in body
    finally:
        _cleanup_student(student['id'])


@pytest.mark.django_db
def test_balance_nonexistent_student_returns_200(admin_client):
    """Express не проверяет существование при /balance — возвращает пустые данные."""
    resp = admin_client.get(f'{BASE_URL}/999999999/balance')
    assert resp.status_code == 200
    body = resp.json()
    assert body['paid_by_direction'] == []
    assert body['attended_by_direction'] == []
    assert body['total_balance'] == 0
    assert body['total_paid_amount'] == 0
    assert body['payments'] == []


# ---------------------------------------------------------------------------
# POST /api/admin/students — create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_returns_201(admin_client):
    payload = _student_payload(full_name='__test_post_201__')
    resp = admin_client.post(BASE_URL, payload, format='json')
    if resp.status_code == 201:
        _cleanup_student(resp.json()['id'])
    assert resp.status_code == 201


@pytest.mark.django_db
def test_create_persists_in_db(admin_client):
    from apps.students import repository
    payload = _student_payload(full_name='__test_post_db__')
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 201
    sid = resp.json()['id']
    try:
        fetched = repository.get_student(sid)
        assert fetched is not None
        assert fetched['full_name'] == '__test_post_db__'
    finally:
        _cleanup_student(sid)


@pytest.mark.django_db
def test_create_missing_full_name_returns_400(admin_client):
    resp = admin_client.post(BASE_URL, {'parent1_phone': '+79001234567'}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_invalid_parent1_email_returns_400(admin_client):
    """parent1_email — EmailField: некорректный email → 400."""
    payload = _student_payload(
        full_name='__test_post_bad_email__',
        parent1_email='not-an-email',
    )
    resp = admin_client.post(BASE_URL, payload, format='json')
    if resp.status_code == 201:
        _cleanup_student(resp.json()['id'])
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_invalid_crm_link_returns_400(admin_client):
    """crm_link — URLField (strict): ссылка без схемы → 400."""
    payload = _student_payload(
        full_name='__test_post_bad_link__',
        crm_link='bitrix24.example/crm/1',
    )
    resp = admin_client.post(BASE_URL, payload, format='json')
    if resp.status_code == 201:
        _cleanup_student(resp.json()['id'])
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_with_birth_date(admin_client):
    # Поле age удалено — возраст считается на фронте из birth_date; сохраняем дату.
    payload = _student_payload(
        full_name='__test_post_birth__',
        birth_date='2011-08-31',
    )
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 201
    body = resp.json()
    try:
        assert body['birth_date'] == '2011-08-31'
        assert 'age' not in body
    finally:
        _cleanup_student(body['id'])


# ---------------------------------------------------------------------------
# PATCH /api/admin/students/:id — update
# ---------------------------------------------------------------------------

@pytest.fixture
def existing_student():
    """Создаёт студента и удаляет его после теста."""
    from apps.students import repository
    student = repository.create_student({'full_name': '__test_patch_student__'})
    yield student
    _cleanup_student(student['id'])


@pytest.mark.django_db
def test_patch_returns_200(admin_client, existing_student):
    resp = admin_client.patch(
        f"{BASE_URL}/{existing_student['id']}",
        {'full_name': '__test_patch_name_new__'},
        format='json',
    )
    assert resp.status_code == 200


@pytest.mark.django_db
def test_patch_updates_full_name(admin_client, existing_student):
    resp = admin_client.patch(
        f"{BASE_URL}/{existing_student['id']}",
        {'full_name': '__test_patch_name_updated__'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['full_name'] == '__test_patch_name_updated__'


@pytest.mark.django_db
def test_patch_nonexistent_returns_404(admin_client):
    resp = admin_client.patch(
        f'{BASE_URL}/999999999',
        {'full_name': 'ghost'},
        format='json',
    )
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_patch_birth_date(admin_client, existing_student):
    resp = admin_client.patch(
        f"{BASE_URL}/{existing_student['id']}",
        {'birth_date': '2016-02-01'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['birth_date'] == '2016-02-01'
    assert 'age' not in resp.json()


# ---------------------------------------------------------------------------
# DELETE /api/admin/students/:id — удалён вместе со статусом not_enrolled
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_delete_method_not_allowed(admin_client):
    """Soft-delete ученика убран: уход оформляется стадией сделки продления
    («Ушёл»), а не архивацией ученика. Если DELETE вернут обратно, этот тест
    упадёт и заставит осознанно пересмотреть решение."""
    from apps.students import repository
    student = repository.create_student({'full_name': '__test_del_405__'})
    try:
        resp = admin_client.delete(f"{BASE_URL}/{student['id']}")
        assert resp.status_code == 405
        # Ученик на месте — молчаливой архивации не случилось.
        assert repository.get_student(student['id']) is not None
    finally:
        _cleanup_student(student['id'])
