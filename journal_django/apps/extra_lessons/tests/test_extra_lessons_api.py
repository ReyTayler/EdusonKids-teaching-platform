"""
E2E тесты для /api/admin/extra-lessons (admin) и /api/extra-lessons (teacher).

Паттерн — как apps/lessons/tests/test_lessons_api.py: root conftest.py даёт
admin_client/manager_client/teacher_client/superadmin_client/anon_client (JWT
access-cookie для СВЕЖЕГО, случайного аккаунта на каждый вызов фикстуры).

Для teacher-скоуп тестов (владелец назначения vs чужой) нужен teacher-аккаунт,
привязанный к КОНКРЕТНОМУ teacher_id (не случайному) — по аналогии с
apps/teacher_spa/tests/conftest.py::account_fixture. Локальный хелпер
_teacher_client_for(teacher_id) создаёт такой аккаунт через api_client_for
(root conftest.py) + apps.accounts.models.Account.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection

pytestmark = pytest.mark.django_db

ADMIN_URL = '/api/admin/extra-lessons'
TEACHER_URL = '/api/extra-lessons'


def _created_id(resp):
    """id первой созданной резолюции из ответа POST /extra-lessons."""
    return resp.data['resolution_ids'][0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _teacher_account_for(teacher_id: int, email: str) -> int:
    """Создаёт account с role='teacher', привязанный к КОНКРЕТНОМУ teacher_id."""
    pw = make_password('testpass_sentinel')
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO accounts
                (email, password, role, teacher_id, is_active, is_staff, is_superuser,
                 first_name, last_name, token_version, date_joined)
            VALUES (%s, %s, 'teacher', %s, true, false, false, '', '', 0, NOW())
            RETURNING id
            """,
            [email, pw, teacher_id],
        )
        return cur.fetchone()[0]


def _delete_account(account_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s', [account_id, account_id])
        cur.execute('DELETE FROM accounts WHERE id = %s', [account_id])


@pytest.fixture
def teacher_client_for(api_client_for, teacher_fixture, other_teacher_fixture):
    """teacher_client_for(teacher_id) → APIClient JWT для аккаунта, привязанного
    к ЭТОМУ teacher_id (в отличие от root teacher_client — случайный teacher).

    Явно зависит от teacher_fixture/other_teacher_fixture (даже если тест
    использует только один из них) — иначе нет гарантии, что наш account
    (FK teacher_id, ON DELETE NO ACTION в БД) будет удалён РАНЬШЕ, чем сам
    teacher-фикстура снесёт свою строку teachers, и DELETE FROM teachers
    упадёт по внешнему ключу. Явная fixture-зависимость гарантирует нужный
    порядок teardown (pytest: зависимость переживает зависящую фикстуру).
    """
    from apps.accounts.models import Account

    created_ids: list[int] = []

    def _make(teacher_id: int, email: str):
        acc_id = _teacher_account_for(teacher_id, email)
        created_ids.append(acc_id)
        account = Account.objects.get(pk=acc_id)
        return api_client_for(account)

    yield _make
    for acc_id in created_ids:
        _delete_account(acc_id)


@pytest.fixture
def cleanup_resolutions(missed_lesson_fixture):
    """Сносит резолюции (absence_resolutions), созданные тестом за этот пропуск.

    conftest.missed_lesson_fixture в teardown чистит только СТАРУЮ таблицу
    extra_lesson_assignments, а absence_resolutions.{missed_lesson,
    assigned_teacher} — реальные FK. Оставленная строка ссылается на уже
    удалённого teacher_fixture-учителя → deferred-проверка при teardown
    db-фикстуры (SET CONSTRAINTS ALL IMMEDIATE) падает. Зависимость от
    missed_lesson_fixture гарантирует, что этот teardown отработает РАНЬШЕ,
    чем снесут учителя/урок."""
    yield
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM absence_resolutions WHERE missed_lesson_id = %s',
            [missed_lesson_fixture],
        )


def _create_payload(missed_lesson_id, teacher_id, student_id, date='2026-04-05'):
    return {
        'missed_lesson_id': missed_lesson_id,
        'teacher_id': teacher_id,
        'student_ids': [student_id],
        'scheduled_date': date,
        'scheduled_time': '15:00',
        'duration_minutes': 45,
    }


# ---------------------------------------------------------------------------
# Admin: create — RBAC
# ---------------------------------------------------------------------------

def test_manager_can_create_assignment(
    manager_client, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    resp = manager_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    assert resp.status_code == 201
    assert resp.data['created'] == 1


def test_admin_can_create_assignment(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    assert resp.status_code == 201
    assert resp.data['created'] == 1


def test_teacher_cannot_create_assignment(
    teacher_client, teacher_fixture, missed_lesson_fixture, student_fixture,
):
    resp = teacher_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    assert resp.status_code == 403


def test_unauthenticated_gets_401(
    anon_client, teacher_fixture, missed_lesson_fixture, student_fixture,
):
    resp = anon_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    assert resp.status_code == 401


def test_create_missing_missed_lesson_400(
    admin_client, teacher_fixture, student_fixture,
):
    resp = admin_client.post(
        ADMIN_URL,
        _create_payload(999_999_999, teacher_fixture, student_fixture),
        format='json',
    )
    assert resp.status_code == 400


def test_create_invalid_body_400(admin_client):
    resp = admin_client.post(ADMIN_URL, {'missed_lesson_id': 'not-an-int'}, format='json')
    assert resp.status_code == 400


def test_create_blocked_when_student_was_not_absent(
    admin_client, teacher_fixture, missed_lesson_fixture,
):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name) "
            "VALUES ('__el_api_present_student__') RETURNING id"
        )
        present_student_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
            [missed_lesson_fixture, present_student_id],
        )
    try:
        resp = admin_client.post(
            ADMIN_URL,
            _create_payload(missed_lesson_fixture, teacher_fixture, present_student_id),
            format='json',
        )
        assert resp.status_code == 400
        assert 'error' in resp.data
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE student_id = %s', [present_student_id])
            cur.execute('DELETE FROM students WHERE id = %s', [present_student_id])


@pytest.mark.usefixtures('payment_balance_guard_on')
def test_create_blocked_when_student_has_no_paid_balance(
    admin_client, teacher_fixture, missed_lesson_unpaid_fixture, unpaid_student_fixture,
):
    resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_unpaid_fixture, teacher_fixture, unpaid_student_fixture),
        format='json',
    )
    assert resp.status_code == 400
    assert 'error' in resp.data


def test_create_duplicate_assignment_409(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    """Test creating a duplicate assignment (same missed_lesson + student) returns 409."""
    payload = _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture)

    # Create first assignment
    resp1 = admin_client.post(ADMIN_URL, payload, format='json')
    assert resp1.status_code == 201

    # Attempt to create duplicate assignment for same student against same missed_lesson
    resp2 = admin_client.post(ADMIN_URL, payload, format='json')
    assert resp2.status_code == 409
    assert 'error' in resp2.data


# ---------------------------------------------------------------------------
# Admin: list / detail
# ---------------------------------------------------------------------------

def test_list_contract(admin_client):
    resp = admin_client.get(ADMIN_URL)
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {'rows', 'total', 'page', 'page_size'}


def test_list_filters_by_student_and_group_name(
    admin_client, missed_lesson_fixture, student_fixture, group_fixture, cleanup_resolutions,
):
    """Фильтры шапки списка (?student_name / ?missed_lesson_group_name) — частичное
    сравнение без регистра, сужают выборку до совпадающих резолюций.
    missed_lesson_fixture авто-создал pending для __el_test_student__ в __el_test_group__."""
    def _rid_present(url: str) -> bool:
        rows = admin_client.get(url).json()['rows']
        return any(r['missed_lesson_id'] == missed_lesson_fixture
                   and r['student_id'] == student_fixture for r in rows)

    # Совпадающий частичный (lower-case) фильтр по имени ученика — строка видна.
    assert _rid_present(f'{ADMIN_URL}?student_name=el_test_student')
    # Несовпадающий — строки нет.
    assert not _rid_present(f'{ADMIN_URL}?student_name=__no_such_student__')
    # Фильтр по имени группы пропуска.
    assert _rid_present(f'{ADMIN_URL}?missed_lesson_group_name=el_test_group')
    assert not _rid_present(f'{ADMIN_URL}?missed_lesson_group_name=__no_such_group__')


def test_list_filter_by_status(admin_client, missed_lesson_fixture, student_fixture, cleanup_resolutions):
    """?status=pending включает авто-созданный пропуск; ?status=makeup_done — нет."""
    def _rid_present(url: str) -> bool:
        rows = admin_client.get(url).json()['rows']
        return any(r['missed_lesson_id'] == missed_lesson_fixture
                   and r['student_id'] == student_fixture for r in rows)

    assert _rid_present(f'{ADMIN_URL}?status=pending')
    assert not _rid_present(f'{ADMIN_URL}?status=makeup_done')


def test_get_detail_200(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    resp = admin_client.get(f'{ADMIN_URL}/{rid}')
    assert resp.status_code == 200
    assert resp.data['id'] == rid


def test_get_detail_404(admin_client):
    resp = admin_client.get(f'{ADMIN_URL}/999999999')
    assert resp.status_code == 404


def test_pending_queue_and_assign_transition(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    """missed_lesson_fixture (ученик present=false) авто-создал pending. Он виден
    в очереди ?status=pending; назначение переводит ЭТУ строку в
    makeup_scheduled (без дубля)."""
    lst = admin_client.get(f'{ADMIN_URL}?status=pending').json()
    mine = [r for r in lst['rows']
            if r['missed_lesson_id'] == missed_lesson_fixture and r['student_id'] == student_fixture]
    assert len(mine) == 1
    assert mine[0]['status'] == 'pending'

    resp = admin_client.post(
        ADMIN_URL, _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json')
    assert resp.status_code == 201
    rid = resp.data['resolution_ids'][0]
    assert rid == mine[0]['id']  # та же строка, не новая
    detail = admin_client.get(f'{ADMIN_URL}/{rid}')
    assert detail.data['status'] == 'makeup_scheduled'


# ---------------------------------------------------------------------------
# Admin: cancel
# ---------------------------------------------------------------------------

def test_cancel_happy_path(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    resp = admin_client.post(f'{ADMIN_URL}/{rid}/cancel')
    assert resp.status_code == 200
    assert resp.data['status'] == 'pending'


def test_cancel_404(admin_client):
    resp = admin_client.post(f'{ADMIN_URL}/999999999/cancel')
    assert resp.status_code == 404


def test_cancel_conflict_when_already_cancelled(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    admin_client.post(f'{ADMIN_URL}/{rid}/cancel')
    resp = admin_client.post(f'{ADMIN_URL}/{rid}/cancel')
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Admin: delete (fact) — 404 / 409 (not done yet)
# ---------------------------------------------------------------------------

def test_delete_404(admin_client):
    resp = admin_client.delete(f'{ADMIN_URL}/999999999')
    assert resp.status_code == 404


def test_delete_forbidden_for_manager(manager_client):
    """Откат факта (проведённый доп.урок / сгорание) двигает баланс ученика и
    зарплату преподавателя — менеджеру закрыт (решение 2026-07-30).
    RBAC проверяется до логики вьюхи, поэтому 403 приходит независимо от id."""
    resp = manager_client.delete(f'{ADMIN_URL}/999999999')
    assert resp.status_code == 403


def test_detail_get_still_allowed_for_manager(manager_client):
    """Закрыт только откат: карточку менеджер по-прежнему открывает (иначе
    пропала бы и кнопка, а она должна быть видна, но неактивна)."""
    resp = manager_client.get(f'{ADMIN_URL}/999999999')
    assert resp.status_code == 404  # не 403 — доступ есть, записи нет


def test_delete_conflict_when_not_done(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    resp = admin_client.delete(f'{ADMIN_URL}/{rid}')
    assert resp.status_code == 409


def test_delete_happy_path_when_done(
    admin_client, teacher_client_for, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    """Test DELETE on a done assignment returns 204 and cleans up fact_lesson."""
    # Create assignment
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    assignment_id = _created_id(create_resp)

    # Record the assignment (creates fact_lesson, sets status to done)
    owner_client = teacher_client_for(teacher_fixture, '__el_delete_test__@test.local')
    record_resp = owner_client.post(
        f'{TEACHER_URL}/{assignment_id}/record',
        {'present': True, 'record_url': None},
        format='json',
    )
    assert record_resp.status_code == 200
    fact_lesson_id = record_resp.data['lesson_id']

    try:
        # DELETE the assignment (should delete the fact_lesson)
        resp = admin_client.delete(f'{ADMIN_URL}/{assignment_id}')
        assert resp.status_code == 204

        # Verify the assignment still exists but status is back to scheduled
        get_resp = admin_client.get(f'{ADMIN_URL}/{assignment_id}')
        assert get_resp.status_code == 200
        assert get_resp.data['status'] == 'pending'
        assert get_resp.data['fact_lesson_id'] is None
    finally:
        # Cleanup: delete lesson if it still exists
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [fact_lesson_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [fact_lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [fact_lesson_id])


# ---------------------------------------------------------------------------
# Teacher: detail scope — own (200) vs. another teacher's (404, not 403)
# ---------------------------------------------------------------------------

def test_teacher_can_get_own_assignment(
    admin_client, teacher_client_for, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    owner_client = teacher_client_for(teacher_fixture, '__el_owner__@test.local')
    resp = owner_client.get(f'{TEACHER_URL}/{rid}')
    assert resp.status_code == 200
    assert resp.data['id'] == rid


def test_teacher_gets_404_for_another_teachers_assignment(
    admin_client, teacher_client_for, teacher_fixture, other_teacher_fixture,
    missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    stranger_client = teacher_client_for(other_teacher_fixture, '__el_stranger__@test.local')
    resp = stranger_client.get(f'{TEACHER_URL}/{rid}')
    # Единый 404 — не раскрываем чужим существование назначения (не 403).
    assert resp.status_code == 404


def test_teacher_detail_404_for_nonexistent(teacher_client):
    resp = teacher_client.get(f'{TEACHER_URL}/999999999')
    assert resp.status_code == 404


def test_manager_cannot_use_teacher_endpoint(manager_client):
    resp = manager_client.get(f'{TEACHER_URL}/1')
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Teacher: record
# ---------------------------------------------------------------------------

def test_teacher_can_record_own_assignment(
    admin_client, teacher_client_for, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    owner_client = teacher_client_for(teacher_fixture, '__el_owner2__@test.local')
    try:
        resp = owner_client.post(
            f'{TEACHER_URL}/{rid}/record',
            {'present': True, 'record_url': None},
            format='json',
        )
        assert resp.status_code == 200
        assert 'lesson_id' in resp.data
        assert 'payment' in resp.data
        assert 'penalty' in resp.data
    finally:
        with connection.cursor() as cur:
            cur.execute(
                'SELECT fact_lesson_id FROM absence_resolutions WHERE id = %s',
                [rid],
            )
            row = cur.fetchone()
            fact_lesson_id = row[0] if row else None
        if fact_lesson_id:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [fact_lesson_id])
                cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [fact_lesson_id])
                cur.execute('DELETE FROM lessons WHERE id = %s', [fact_lesson_id])


def test_teacher_cannot_record_another_teachers_assignment(
    admin_client, teacher_client_for, teacher_fixture, other_teacher_fixture,
    missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    stranger_client = teacher_client_for(other_teacher_fixture, '__el_stranger2__@test.local')
    resp = stranger_client.post(
        f'{TEACHER_URL}/{rid}/record',
        {'present': True, 'record_url': None},
        format='json',
    )
    assert resp.status_code == 403


def test_record_404_for_nonexistent(teacher_client, student_fixture):
    resp = teacher_client.post(
        f'{TEACHER_URL}/999999999/record',
        {'present': True, 'record_url': None},
        format='json',
    )
    assert resp.status_code == 404


def test_record_conflict_when_already_done(
    admin_client, teacher_client_for, teacher_fixture, missed_lesson_fixture, student_fixture,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    owner_client = teacher_client_for(teacher_fixture, '__el_owner3__@test.local')
    body = {'present': True, 'record_url': None}
    first = owner_client.post(f'{TEACHER_URL}/{rid}/record', body, format='json')
    assert first.status_code == 200
    try:
        second = owner_client.post(f'{TEACHER_URL}/{rid}/record', body, format='json')
        assert second.status_code == 409
    finally:
        fact_lesson_id = first.data['lesson_id']
        with connection.cursor() as cur:
            # Резолюция (status=done) держит fact_lesson (SET_NULL — ORM-семантика,
            # DB-FK не каскадит) И missed_lesson — снести её ДО удаления урока.
            cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [missed_lesson_fixture])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [fact_lesson_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [fact_lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [fact_lesson_id])


@pytest.mark.usefixtures('payment_balance_guard_on')
def test_record_blocked_when_student_balance_dropped(
    admin_client, teacher_client_for, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE student_id = %s', [student_fixture])
    owner_client = teacher_client_for(teacher_fixture, '__el_unpaid_record__@test.local')
    resp = owner_client.post(
        f'{TEACHER_URL}/{rid}/record',
        {'present': True, 'record_url': None},
        format='json',
    )
    assert resp.status_code == 400
    assert 'error' in resp.data


def test_record_absent_student_blocked_400(
    admin_client, teacher_client_for, teacher_fixture, missed_lesson_fixture, student_fixture,
    cleanup_resolutions,
):
    """present=false («ученик не пришёл») → 400, доп.урок не записывается: неявку
    оформляют «Отменой» назначения, а не записью с present=false."""
    create_resp = admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    rid = _created_id(create_resp)
    owner_client = teacher_client_for(teacher_fixture, '__el_absent_record__@test.local')
    resp = owner_client.post(
        f'{TEACHER_URL}/{rid}/record',
        {'present': False, 'record_url': None},
        format='json',
    )
    assert resp.status_code == 400
    assert 'error' in resp.data

    # Резолюция осталась scheduled, факт-урок не создан.
    get_resp = admin_client.get(f'{ADMIN_URL}/{rid}')
    assert get_resp.status_code == 200
    assert get_resp.data['status'] == 'makeup_scheduled'
    assert get_resp.data['fact_lesson_id'] is None


def test_record_invalid_body_400(teacher_client):
    resp = teacher_client.post(f'{TEACHER_URL}/1/record', {}, format='json')
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Admin: burn (сжечь) — Phase 1c-2
# ---------------------------------------------------------------------------

def _pending_rid(client, missed_lesson_id, student_id):
    lst = client.get(f'{ADMIN_URL}?status=pending').json()
    for r in lst['rows']:
        if r['missed_lesson_id'] == missed_lesson_id and r['student_id'] == student_id:
            return r['id']
    raise AssertionError('pending resolution not found')


def _cleanup_burn(missed_lesson_id, fact_id):
    with connection.cursor() as cur:
        # Резолюция держит fact_lesson_id и missed_lesson — снести её ДО факта.
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [missed_lesson_id])
        if fact_id:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [fact_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [fact_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [fact_id])


def test_burn_endpoint_burns_pending(
    admin_client, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    rid = _pending_rid(admin_client, missed_lesson_fixture, student_fixture)
    fact_id = None
    try:
        resp = admin_client.post(f'{ADMIN_URL}/{rid}/burn')
        assert resp.status_code == 200
        assert resp.data['payment'] == 200
        detail = admin_client.get(f'{ADMIN_URL}/{rid}')
        assert detail.data['status'] == 'burned'
        fact_id = detail.data['fact_lesson_id']
        assert fact_id is not None
    finally:
        _cleanup_burn(missed_lesson_fixture, fact_id)


def test_burn_endpoint_conflict_when_not_pending(
    admin_client, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    rid = _pending_rid(admin_client, missed_lesson_fixture, student_fixture)
    fact_id = None
    try:
        first = admin_client.post(f'{ADMIN_URL}/{rid}/burn')
        assert first.status_code == 200
        fact_id = admin_client.get(f'{ADMIN_URL}/{rid}').data['fact_lesson_id']
        second = admin_client.post(f'{ADMIN_URL}/{rid}/burn')
        assert second.status_code == 409
    finally:
        _cleanup_burn(missed_lesson_fixture, fact_id)


def test_burn_endpoint_404_for_nonexistent(admin_client):
    resp = admin_client.post(f'{ADMIN_URL}/999999999/burn')
    assert resp.status_code == 404


def test_burn_endpoint_requires_manager(teacher_client):
    # RBAC проверяется до логики вьюхи — teacher получает 403 независимо от rid.
    resp = teacher_client.post(f'{ADMIN_URL}/1/burn')
    assert resp.status_code == 403


def test_burn_endpoint_unauthenticated_401(anon_client):
    resp = anon_client.post(f'{ADMIN_URL}/1/burn')
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Admin: pending-count (бейдж сайдбара)
# ---------------------------------------------------------------------------

def test_pending_count_reflects_pending_resolutions(
    admin_client, teacher_fixture, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    """missed_lesson_fixture авто-создал 1 pending → счётчик >= 1; назначение
    доп.урока (pending → makeup_scheduled) уменьшает его на 1."""
    resp = admin_client.get(f'{ADMIN_URL}/pending-count')
    assert resp.status_code == 200
    assert isinstance(resp.data['count'], int)
    before = resp.data['count']
    assert before >= 1

    admin_client.post(
        ADMIN_URL,
        _create_payload(missed_lesson_fixture, teacher_fixture, student_fixture),
        format='json',
    )
    after = admin_client.get(f'{ADMIN_URL}/pending-count').json()['count']
    assert after == before - 1


def test_pending_count_requires_manager(teacher_client):
    resp = teacher_client.get(f'{ADMIN_URL}/pending-count')
    assert resp.status_code == 403


def test_list_default_order_puts_pending_first(
    admin_client, teacher_fixture, group_fixture, missed_lesson_fixture,
    student_fixture, cleanup_resolutions,
):
    """
    Список без параметров сортировки — рабочая очередь: «Ждёт решения» сверху,
    внутри блока свежие первыми.

    Проверяется через API, а не только через репозиторий: у вьюхи свой дефолт
    sort_by, и разойдись он с дефолтом репозитория — раздел открывался бы в
    произвольном порядке, а тесты репозитория остались бы зелёными.

    Ожидающая резолюция здесь СТАРШЕ назначенной (наверх её выводит статус, а
    не свежесть), а между собой ожидающие расставлены против порядка вставки —
    иначе тест не отличал бы сортировку по дате создания от сортировки по id.
    """
    import datetime as dt

    from apps.extra_lessons import repository
    from apps.extra_lessons.models import AbsenceResolution

    def _created(resolution_id, when):
        AbsenceResolution.objects.filter(id=resolution_id).update(created_at=when)

    def _extra(**kwargs):
        return repository.create_extra_direct(
            group_id=group_fixture, student_id=student_fixture,
            assigned_teacher_id=teacher_fixture, scheduled_date=dt.date(2026, 4, 20),
            scheduled_time=dt.time(15, 0), duration_minutes=45,
            target_lesson_number=None, **kwargs)

    # Ожидающая, заведённая раньше всех (меньший id), но самая свежая по дате.
    fresh_pending = repository.lock_for_assign(missed_lesson_fixture, student_fixture)['id']
    _created(fresh_pending, dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc))
    # Вторая ожидающая: доп.урок сверх курса, возвращённый в «ждёт решения».
    stale_pending = _extra()
    repository.back_to_pending(stale_pending)
    _created(stale_pending, dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    # Назначенный — самый свежий из всех, но статус держит его ниже ожидающих.
    scheduled = _extra()
    _created(scheduled, dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))

    try:
        order = [row['id'] for row in admin_client.get(ADMIN_URL).json()['rows']]
        assert order.index(fresh_pending) < order.index(stale_pending)
        assert order.index(stale_pending) < order.index(scheduled)
    finally:
        # kind='extra' не привязан к пропуску, поэтому cleanup_resolutions
        # (чистит по missed_lesson_id) эти строки не заберёт.
        AbsenceResolution.objects.filter(id__in=[stale_pending, scheduled]).delete()


# ---------------------------------------------------------------------------
# Admin: delete pending — полное удаление заявки «Ждёт решения»
# ---------------------------------------------------------------------------

def _pending_id(missed_lesson_id: int, student_id: int) -> int:
    """id авто-созданной pending-резолюции пропуска (record_lesson завёл её при
    записи missed_lesson_fixture — вторую не создать, UNIQUE(пропуск, ученик))."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM absence_resolutions "
            "WHERE missed_lesson_id = %s AND student_id = %s AND status = 'pending'",
            [missed_lesson_id, student_id],
        )
        return cur.fetchone()[0]


def _resolution_exists(rid: int) -> bool:
    with connection.cursor() as cur:
        cur.execute('SELECT 1 FROM absence_resolutions WHERE id = %s', [rid])
        return cur.fetchone() is not None


def test_delete_pending_removes_row(
    admin_client, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    """DELETE заявки в статусе «Ждёт решения» → 204 и строки в БД больше нет."""
    rid = _pending_id(missed_lesson_fixture, student_fixture)

    resp = admin_client.delete(f'{ADMIN_URL}/{rid}')

    assert resp.status_code == 204
    assert _resolution_exists(rid) is False


def test_delete_pending_forbidden_for_manager(
    manager_client, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    """Удаление заявки — admin/superadmin (ReadStaffWriteAdmin), как и откат
    факта. Менеджеру закрыто: заявка удаляется безвозвратно."""
    rid = _pending_id(missed_lesson_fixture, student_fixture)

    resp = manager_client.delete(f'{ADMIN_URL}/{rid}')

    assert resp.status_code == 403
    assert _resolution_exists(rid) is True


def test_delete_pending_allowed_for_superadmin(
    superadmin_client, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    rid = _pending_id(missed_lesson_fixture, student_fixture)

    resp = superadmin_client.delete(f'{ADMIN_URL}/{rid}')

    assert resp.status_code == 204
    assert _resolution_exists(rid) is False


def test_burn_returns_409_when_student_was_present(
    manager_client, missed_lesson_fixture, student_fixture, cleanup_resolutions,
):
    """Заявка жива, а ученик на пропущенном уроке отмечен «был» (группа ВДГ18,
    №32) — сжигать нечего, иначе урок спишется дважды."""
    with connection.cursor() as cur:
        cur.execute('SELECT id FROM absence_resolutions WHERE missed_lesson_id=%s '
                    'AND student_id=%s', [missed_lesson_fixture, student_fixture])
        rid = cur.fetchone()[0]
        cur.execute('UPDATE lesson_attendance SET present=true '
                    'WHERE lesson_id=%s AND student_id=%s',
                    [missed_lesson_fixture, student_fixture])
    resp = manager_client.post(f'{ADMIN_URL}/{rid}/burn', format='json')
    assert resp.status_code == 409
    assert 'не были отмечены отсутствующими' in resp.json()['error']
