"""
E2E тесты для /api/admin/payments.

Проверяют HTTP-статусы, коды ошибок POST, DELETE с warning, валидацию.
Используют реальную БД и реальный Django test client.
"""
from __future__ import annotations

import pytest
from django.db import connection

BASE_URL = '/api/admin/payments'


def _payment_payload(student_id: int, direction_id: int, **overrides) -> dict:
    return {
        'student_id': student_id,
        'direction_id': direction_id,
        'lessons_count': 4,
        'total_amount': '2000.00',
        'paid_at': '2026-03-01',
        **overrides,
    }


# ---------------------------------------------------------------------------
# Auth / authorization
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_cookie_returns_401(anon_client):
    resp = anon_client.get(BASE_URL)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_teacher_returns_403(teacher_client):
    resp = teacher_client.get(BASE_URL)
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_list_returns_200(admin_client):
    resp = admin_client.get(BASE_URL)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.django_db
def test_manager_list_returns_200(manager_client):
    resp = manager_client.get(BASE_URL)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# RBAC мутаций: деньги заводит и стирает только админ (решение 2026-07-28).
# Менеджер оплаты видит (тесты выше), но не создаёт и не удаляет.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_manager_post_returns_403(manager_client, direction_fixture, student_fixture):
    payload = _payment_payload(student_fixture, direction_fixture)
    resp = manager_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 403
    # Ничего не записалось — проверяем факт, а не только код ответа.
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM payments WHERE student_id = %s', [student_fixture])
        assert cur.fetchone()[0] == 0


@pytest.mark.django_db
def test_manager_delete_returns_403(manager_client, payment_fixture):
    resp = manager_client.delete(f'{BASE_URL}/{payment_fixture}')
    assert resp.status_code == 403
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM payments WHERE id = %s', [payment_fixture])
        assert cur.fetchone()[0] == 1, 'оплата не должна быть удалена'


@pytest.mark.django_db
def test_manager_get_detail_returns_200(manager_client, payment_fixture):
    """Чтение менеджеру остаётся: блокируются только мутации."""
    resp = manager_client.get(f'{BASE_URL}/{payment_fixture}')
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET list filters
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_with_student_filter(admin_client, payment_fixture, student_fixture):
    resp = admin_client.get(f'{BASE_URL}?student_id={student_fixture}')
    assert resp.status_code == 200
    data = resp.json()
    assert any(p['id'] == payment_fixture for p in data)
    for p in data:
        assert p['student_id'] == student_fixture


@pytest.mark.django_db
def test_list_with_direction_filter(admin_client, payment_fixture, direction_fixture):
    resp = admin_client.get(f'{BASE_URL}?direction_id={direction_fixture}')
    assert resp.status_code == 200
    data = resp.json()
    assert any(p['id'] == payment_fixture for p in data)


@pytest.mark.django_db
def test_list_from_filter_excludes_earlier(admin_client, payment_fixture, student_fixture):
    resp = admin_client.get(f'{BASE_URL}?student_id={student_fixture}&from=2026-01-02')
    assert resp.status_code == 200
    assert not any(p['id'] == payment_fixture for p in resp.json())


# ---------------------------------------------------------------------------
# GET /:id
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_existing_returns_200(admin_client, payment_fixture):
    resp = admin_client.get(f'{BASE_URL}/{payment_fixture}')
    assert resp.status_code == 200
    assert resp.json()['id'] == payment_fixture


@pytest.mark.django_db
def test_get_nonexistent_returns_404(admin_client):
    resp = admin_client.get(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


# ---------------------------------------------------------------------------
# POST — create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_post_success_returns_201(admin_client, direction_fixture, student_fixture):
    payload = _payment_payload(student_fixture, direction_fixture)
    resp = admin_client.post(BASE_URL, payload, format='json')
    if resp.status_code == 201:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payments WHERE id = %s', [resp.json()['id']])
    assert resp.status_code == 201


@pytest.mark.django_db
def test_post_returns_payment_fields(admin_client, direction_fixture, student_fixture):
    payload = _payment_payload(student_fixture, direction_fixture)
    resp = admin_client.post(BASE_URL, payload, format='json')
    data = resp.json()
    if resp.status_code == 201:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payments WHERE id = %s', [data['id']])
    assert resp.status_code == 201
    assert data['student_id'] == student_fixture
    assert data['direction_id'] == direction_fixture
    assert data['subscriptions_count'] == 1


@pytest.mark.django_db
def test_post_direction_not_found_returns_404(admin_client, student_fixture):
    payload = _payment_payload(student_fixture, 999_999_999)
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Direction not found'}


@pytest.mark.django_db
def test_post_no_capacity_returns_400(admin_client, student_fixture):
    """Направление без total_lessons → no_capacity."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, active) VALUES ('__api_nocap__', true) RETURNING id",
        )
        dir_id = cur.fetchone()[0]
    try:
        payload = _payment_payload(student_fixture, dir_id)
        resp = admin_client.post(BASE_URL, payload, format='json')
        assert resp.status_code == 400
        body = resp.json()
        assert body['error'] == 'no_capacity'
        assert 'message' in body
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM directions WHERE id = %s', [dir_id])


@pytest.mark.django_db
def test_post_cap_exceeded_returns_400(admin_client, direction_fixture, student_fixture):
    """cap = floor(8/4) = 2. После 2 оплат → cap_exceeded."""
    created_ids = []
    try:
        for _ in range(2):
            resp = admin_client.post(
                BASE_URL,
                _payment_payload(student_fixture, direction_fixture),
                format='json',
            )
            assert resp.status_code == 201
            created_ids.append(resp.json()['id'])

        resp = admin_client.post(
            BASE_URL,
            _payment_payload(student_fixture, direction_fixture),
            format='json',
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body['error'] == 'cap_exceeded'
        assert body['already'] == 8
        assert body['cap_subscriptions'] == 2
    finally:
        with connection.cursor() as cur:
            for pid in created_ids:
                cur.execute('DELETE FROM payments WHERE id = %s', [pid])


@pytest.mark.django_db
def test_post_invalid_lessons_count_returns_400(admin_client, direction_fixture, student_fixture):
    # Любое целое ≥ 1 допустимо (см. test_arbitrary_lessons_count) — невалиден только 0.
    payload = _payment_payload(student_fixture, direction_fixture, lessons_count=0)
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_post_missing_required_field_returns_400(admin_client, direction_fixture, student_fixture):
    payload = {'student_id': student_fixture, 'direction_id': direction_fixture}
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /:id
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_delete_returns_200_with_deleted_true(admin_client, direction_fixture, student_fixture):
    resp = admin_client.post(
        BASE_URL,
        _payment_payload(student_fixture, direction_fixture),
        format='json',
    )
    assert resp.status_code == 201
    pid = resp.json()['id']

    del_resp = admin_client.delete(f'{BASE_URL}/{pid}')
    assert del_resp.status_code == 200
    body = del_resp.json()
    assert body['deleted'] is True
    assert 'new_balance' in body


@pytest.mark.django_db
def test_delete_nonexistent_returns_404(admin_client):
    resp = admin_client.delete(f'{BASE_URL}/999999999')
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


@pytest.mark.django_db
def test_delete_adds_warning_when_balance_negative(
    admin_client,
    direction_fixture,
    student_fixture,
    membership_fixture,
    teacher_id_fixture,
):
    """
    Ситуация: attended > purchased → new_balance < 0 → warning: 'balance_negative'.
    Создаём 1 оплату (4 урока), добавляем 5 посещений, удаляем оплату → баланс -5.
    """
    # Купить 1 подписку = 4 урока
    resp = admin_client.post(
        BASE_URL,
        _payment_payload(student_fixture, direction_fixture),
        format='json',
    )
    assert resp.status_code == 201
    pid = resp.json()['id']

    # Получаем group_id из membership
    with connection.cursor() as cur:
        cur.execute('SELECT group_id FROM group_memberships WHERE id = %s', [membership_fixture])
        group_id = cur.fetchone()[0]

    lesson_ids = []
    try:
        # Вставляем 5 уроков + посещений для отрицательного баланса после удаления оплаты
        with connection.cursor() as cur:
            for i in range(5):
                cur.execute(
                    """
                    INSERT INTO lessons
                       (group_id, teacher_id, lesson_date, lesson_number,
                        lesson_duration_minutes, lesson_type, submitted_by_token)
                    VALUES (%s, %s, %s, %s, 60, 'group', 'test-token')
                    RETURNING id
                    """,
                    [group_id, teacher_id_fixture, f'2026-02-{i + 1:02d}', i + 10],
                )
                lid = cur.fetchone()[0]
                lesson_ids.append(lid)
                cur.execute(
                    'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
                    [lid, student_fixture],
                )

        del_resp = admin_client.delete(f'{BASE_URL}/{pid}')
        assert del_resp.status_code == 200
        body = del_resp.json()
        assert body['deleted'] is True
        assert body['new_balance'] < 0
        assert body.get('warning') == 'balance_negative'

    finally:
        with connection.cursor() as cur:
            for lid in lesson_ids:
                cur.execute(
                    'DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                    [lid, student_fixture],
                )
                cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        # pid уже удалён через DELETE endpoint


def test_create_payment_stores_author(admin_client, student_fixture, direction_fixture):
    import json

    from django.db import connection
    resp = admin_client.post('/api/admin/payments', json.dumps({
        'student_id': student_fixture, 'direction_id': direction_fixture,
        'lessons_count': 4, 'total_amount': '4000.00', 'paid_at': '2026-01-01',
    }), content_type='application/json')
    assert resp.status_code == 201, resp.content
    body = resp.json()
    cb = body['created_by']
    assert cb and not cb.startswith('acct:')  # a name/email, not the old machine token
    # cleanup (FK RESTRICT): delete the created payment before teardown
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE id = %s', [body['id']])


def test_prepayment_two_lessons(admin_client, student_fixture, direction_fixture):
    import json
    from django.db import connection
    resp = admin_client.post('/api/admin/payments', json.dumps({
        'student_id': student_fixture, 'direction_id': direction_fixture,
        'lessons_count': 2, 'total_amount': '2000.00', 'paid_at': '2026-01-01',
    }), content_type='application/json')
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body['lessons_count'] == 2
    assert body['subscriptions_count'] is None
    assert str(body['unit_price']) == '1000.00'
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE id = %s', [body['id']])


@pytest.mark.parametrize('lessons, total, unit', [
    (5, '5000.00', '1000.00'),
    (7, '7700.00', '1100.00'),
])
def test_arbitrary_lessons_count(admin_client, student_fixture, direction_fixture,
                                 lessons, total, unit):
    """Любое целое число уроков в пределах курса — не только блоки по 4 и 1–3."""
    import json
    from django.db import connection
    resp = admin_client.post('/api/admin/payments', json.dumps({
        'student_id': student_fixture, 'direction_id': direction_fixture,
        'lessons_count': lessons, 'total_amount': total, 'paid_at': '2026-01-01',
    }), content_type='application/json')
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body['lessons_count'] == lessons
    assert body['subscriptions_count'] is None  # не кратно 4 — абонементов нет
    assert str(body['unit_price']) == unit
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE id = %s', [body['id']])


def test_arbitrary_lessons_count_respects_cap(admin_client, student_fixture, direction_fixture):
    """Курс фикстуры — 8 уроков: 9 поштучно не продаётся."""
    import json
    resp = admin_client.post('/api/admin/payments', json.dumps({
        'student_id': student_fixture, 'direction_id': direction_fixture,
        'lessons_count': 9, 'total_amount': '9000.00', 'paid_at': '2026-01-01',
    }), content_type='application/json')
    assert resp.status_code == 400, resp.content


def test_refund_endpoint_admin(admin_client, student_fixture, direction_fixture):
    import json
    from django.db import connection
    r = admin_client.post('/api/admin/payments', json.dumps({
        'student_id': student_fixture, 'direction_id': direction_fixture,
        'lessons_count': 4, 'total_amount': '4000.00', 'paid_at': '2026-01-01'}),
        content_type='application/json')
    assert r.status_code == 201, r.content
    try:
        resp = admin_client.post(f'/api/admin/students/{student_fixture}/refund', '{}',
                                 content_type='application/json')
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert float(body['refunded_amount']) == 4000.0
        assert body['new_balance'] == 0
        # Возврат раскладывается по направлениям — здесь оно одно.
        assert [r['kind'] for r in body['refunds']] == ['refund']
        assert body['refunds'][0]['direction_id'] == direction_fixture
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payments WHERE student_id = %s', [student_fixture])


def test_refund_endpoint_forbidden_for_manager(manager_client, student_fixture):
    resp = manager_client.post(f'/api/admin/students/{student_fixture}/refund', '{}',
                               content_type='application/json')
    assert resp.status_code == 403


def test_refund_endpoint_forbidden_for_teacher(teacher_client, student_fixture):
    resp = teacher_client.post(f'/api/admin/students/{student_fixture}/refund', '{}',
                               content_type='application/json')
    assert resp.status_code == 403


def test_refund_endpoint_nothing_to_refund(admin_client, student_fixture):
    resp = admin_client.post(f'/api/admin/students/{student_fixture}/refund', '{}',
                             content_type='application/json')
    assert resp.status_code == 400
    assert resp.json()['error'] == 'nothing_to_refund'
