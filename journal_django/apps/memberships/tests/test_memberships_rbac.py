"""
RBAC для /api/admin/memberships (решение 2026-07-28).

  POST   /memberships           набор в группу      — manager/admin/superadmin
  DELETE /memberships/:id       снятие из группы    — manager/admin/superadmin
  PATCH  /memberships/:id       правка полей        — только superadmin
  POST   /memberships/:id/transfer перевод          — только superadmin (см.
                                                      test_transfer_membership.py)

Тесты самодостаточны (свой seed по образцу test_transfer_membership.py) — иначе
на пустой eduson_test они молча скипаются вместе с test_memberships_api.py.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.groups import repository as groups_repo
from apps.memberships import repository

BASE_URL = '/api/admin/memberships'


@pytest.fixture
def seed():
    """Направление, преподаватель, обычная группа и один ученик."""
    ids: dict[str, int] = {}
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, active) "
            "VALUES ('__rbac_dir__', true) RETURNING id"
        )
        ids['direction_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO teachers (name, active, created_at) "
            "VALUES ('__rbac_teacher__', true, NOW()) RETURNING id"
        )
        ids['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES ('__rbac_student__', NOW()) RETURNING id"
        )
        ids['student_id'] = cur.fetchone()[0]

    group = groups_repo.create_group({
        'name': '__rbac_group__',
        'direction_id': ids['direction_id'],
        'teacher_id': ids['teacher_id'],
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
    })
    ids['group_id'] = group['id']

    yield ids

    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [ids['group_id']])
        cur.execute('DELETE FROM groups WHERE id = %s', [ids['group_id']])
        cur.execute('DELETE FROM students WHERE id = %s', [ids['student_id']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [ids['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id = %s', [ids['direction_id']])


def _payload(seed: dict) -> dict:
    return {'group_id': seed['group_id'], 'student_id': seed['student_id']}


# ---------------------------------------------------------------------------
# POST — добавление в группу
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('client_name', ['manager_client', 'admin_client', 'superadmin_client'])
def test_create_allowed_for_staff(request, client_name, seed):
    client = request.getfixturevalue(client_name)
    # POST — UPSERT: у каждого клиента свой прогон фикстуры, но повторный вызов
    # в любом случае вернул бы 201 с реактивацией.
    resp = client.post(BASE_URL, _payload(seed), format='json')
    assert resp.status_code == 201


@pytest.mark.django_db
def test_create_forbidden_for_teacher(teacher_client, seed):
    resp = teacher_client.post(BASE_URL, _payload(seed), format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_create_unauthenticated_401(anon_client, seed):
    resp = anon_client.post(BASE_URL, _payload(seed), format='json')
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE — снятие из группы
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('client_name', ['manager_client', 'admin_client', 'superadmin_client'])
def test_delete_allowed_for_staff(request, client_name, seed):
    client = request.getfixturevalue(client_name)
    membership = repository.add_membership(_payload(seed))

    resp = client.delete(f"{BASE_URL}/{membership['id']}")

    assert resp.status_code == 204
    rows = repository.list_memberships(group_id=seed['group_id'])
    assert rows == []


@pytest.mark.django_db
def test_delete_forbidden_for_teacher(teacher_client, seed):
    membership = repository.add_membership(_payload(seed))
    resp = teacher_client.delete(f"{BASE_URL}/{membership['id']}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PATCH — ручная правка полей остаётся за суперадмином
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('client_name', ['manager_client', 'admin_client'])
def test_patch_forbidden_for_manager_and_admin(request, client_name, seed):
    client = request.getfixturevalue(client_name)
    membership = repository.add_membership(_payload(seed))

    resp = client.patch(f"{BASE_URL}/{membership['id']}", {'lessons_done': 3}, format='json')

    assert resp.status_code == 403


@pytest.mark.django_db
def test_patch_allowed_for_superadmin(superadmin_client, seed):
    membership = repository.add_membership(_payload(seed))
    resp = superadmin_client.patch(
        f"{BASE_URL}/{membership['id']}", {'lessons_done': 3}, format='json'
    )
    assert resp.status_code == 200
