"""
E2E тесты инварианта: индивидуальная группа = максимум 1 активный membership.

Правило (apps/memberships/repository._assert_individual_capacity):
  для группы с is_individual=true нельзя иметь >1 активного membership.
  Нарушение → IndividualGroupFull → view отдаёт 409.

Тесты самодостаточны: сами засевают direction/teacher/двух students в
eduson_test (managed=False), группы создают через repository.create_group,
всё чистят DELETE'ом в teardown.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.groups import repository as groups_repo

BASE_URL = '/api/admin/memberships'


# ---------------------------------------------------------------------------
# Seed-фикстура: направление, преподаватель, два ученика
# ---------------------------------------------------------------------------

@pytest.fixture
def seed():
    """Создаёт direction, teacher и двух students. Возвращает их id. Чистит в teardown."""
    ids: dict[str, int] = {}
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, active) "
            "VALUES ('__il_dir__', true) RETURNING id"
        )
        ids['direction_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO teachers (name, active, created_at) "
            "VALUES ('__il_teacher__', true, NOW()) RETURNING id"
        )
        ids['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES ('__il_student_1__', NOW()) RETURNING id"
        )
        ids['s1'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES ('__il_student_2__', NOW()) RETURNING id"
        )
        ids['s2'] = cur.fetchone()[0]
    yield ids
    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id IN (%s, %s)', [ids['s1'], ids['s2']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [ids['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id = %s', [ids['direction_id']])


def _make_group(seed: dict, is_individual: bool, name: str) -> dict:
    return groups_repo.create_group({
        'name': name,
        'direction_id': seed['direction_id'],
        'teacher_id': seed['teacher_id'],
        'is_individual': is_individual,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
    })


def _cleanup_group(group_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.fixture
def individual_group(seed):
    group = _make_group(seed, True, '__test_individual_limit__')
    yield group
    _cleanup_group(group['id'])


@pytest.fixture
def regular_group(seed):
    group = _make_group(seed, False, '__test_regular_limit__')
    yield group
    _cleanup_group(group['id'])


def _post_member(client, group_id: int, student_id: int):
    return client.post(
        BASE_URL,
        {'group_id': group_id, 'student_id': student_id},
        format='json',
    )


def _set_active(membership_id: int, active: bool) -> None:
    with connection.cursor() as cur:
        cur.execute(
            'UPDATE group_memberships SET active = %s WHERE id = %s',
            [active, membership_id],
        )


def _insert_inactive(group_id: int, student_id: int) -> int:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, 0, false) RETURNING id',
            [group_id, student_id],
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# POST — добавление в индивидуальную группу
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_first_student_into_empty_individual_group_201(superadmin_client, seed, individual_group):
    resp = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert resp.status_code == 201
    assert resp.json()['active'] is True


@pytest.mark.django_db
def test_second_different_student_409(superadmin_client, seed, individual_group):
    r1 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r1.status_code == 201

    r2 = _post_member(superadmin_client, individual_group['id'], seed['s2'])
    assert r2.status_code == 409
    assert 'error' in r2.json()

    # Второй ученик НЕ должен появиться активным (и вообще создаться).
    with connection.cursor() as cur:
        cur.execute(
            'SELECT active FROM group_memberships WHERE group_id = %s AND student_id = %s',
            [individual_group['id'], seed['s2']],
        )
        row = cur.fetchone()
    assert row is None or row[0] is False


@pytest.mark.django_db
def test_repost_same_active_student_reactivation_201(superadmin_client, seed, individual_group):
    """Повторный POST того же уже активного ученика → 201 (UPSERT active=true)."""
    r1 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r1.status_code == 201
    mid = r1.json()['id']

    r2 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r2.status_code == 201
    assert r2.json()['id'] == mid
    assert r2.json()['active'] is True


@pytest.mark.django_db
def test_repost_same_inactive_student_reactivation_201(superadmin_client, seed, individual_group):
    """POST того же ЕДИНСТВЕННОГО (деактивированного) ученика → реактивация ок."""
    r1 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r1.status_code == 201
    mid = r1.json()['id']
    _set_active(mid, False)

    r2 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r2.status_code == 201
    assert r2.json()['id'] == mid
    assert r2.json()['active'] is True


@pytest.mark.django_db
def test_post_other_inactive_student_while_active_409(superadmin_client, seed, individual_group):
    """Второй ученик существует как inactive; активен первый → POST второго → 409."""
    r1 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r1.status_code == 201
    _insert_inactive(individual_group['id'], seed['s2'])

    r2 = _post_member(superadmin_client, individual_group['id'], seed['s2'])
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# PATCH — реактивация через active=true
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_patch_activate_other_while_active_409(superadmin_client, seed, individual_group):
    """PATCH active=true второго membership при активном первом → 409."""
    r1 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r1.status_code == 201
    mid2 = _insert_inactive(individual_group['id'], seed['s2'])

    resp = superadmin_client.patch(f'{BASE_URL}/{mid2}', {'active': True}, format='json')
    assert resp.status_code == 409

    with connection.cursor() as cur:
        cur.execute('SELECT active FROM group_memberships WHERE id = %s', [mid2])
        assert cur.fetchone()[0] is False


@pytest.mark.django_db
def test_patch_activate_only_inactive_200(superadmin_client, seed, individual_group):
    """PATCH active=true единственного (деактивированного) ученика → 200."""
    r1 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    mid = r1.json()['id']
    _set_active(mid, False)

    resp = superadmin_client.patch(f'{BASE_URL}/{mid}', {'active': True}, format='json')
    assert resp.status_code == 200
    assert resp.json()['active'] is True


@pytest.mark.django_db
def test_patch_non_active_field_skips_check(superadmin_client, seed, individual_group):
    """
    PATCH только lessons_done (без active) в занятой инд. группе → 200.

    Даже если в группе уже есть активный — проверка не запускается для PATCH,
    не трогающего active. Обновляем второй (inactive) membership.
    """
    r1 = _post_member(superadmin_client, individual_group['id'], seed['s1'])
    assert r1.status_code == 201
    mid2 = _insert_inactive(individual_group['id'], seed['s2'])

    resp = superadmin_client.patch(f'{BASE_URL}/{mid2}', {'lessons_done': 3}, format='json')
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Групповая группа — лимит не действует
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regular_group_allows_two_active(superadmin_client, seed, regular_group):
    """is_individual=false: двое активных учеников — ок."""
    r1 = _post_member(superadmin_client, regular_group['id'], seed['s1'])
    r2 = _post_member(superadmin_client, regular_group['id'], seed['s2'])
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()['active'] is True
    assert r2.json()['active'] is True
