"""
E2E тесты инварианта: формат группы (is_individual) неизменен после создания.

Правило (apps/groups/repository.update_group):
  PATCH со значением is_individual, отличным от текущего → ImmutableGroupFormat
  → view отдаёт 400 {error: ...}. Совпадающее значение или отсутствие ключа —
  no-op (идемпотентный round-trip PATCH сохранить работоспособным).

Тесты самодостаточны: сами засевают direction/teacher в eduson_test.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.groups import repository as groups_repo

BASE_URL = '/api/admin/groups'


@pytest.fixture
def seed():
    """Создаёт direction и teacher. Возвращает их id. Чистит в teardown."""
    ids: dict[str, int] = {}
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, active) "
            "VALUES ('__imf_dir__', true) RETURNING id"
        )
        ids['direction_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO teachers (name, active, created_at) "
            "VALUES ('__imf_teacher__', true, NOW()) RETURNING id"
        )
        ids['teacher_id'] = cur.fetchone()[0]
    yield ids
    with connection.cursor() as cur:
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
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


def _db_is_individual(group_id: int) -> bool:
    with connection.cursor() as cur:
        cur.execute('SELECT is_individual FROM groups WHERE id = %s', [group_id])
        return cur.fetchone()[0]


@pytest.fixture
def individual_group(seed):
    group = _make_group(seed, True, '__test_immutable_individual__')
    yield group
    _cleanup_group(group['id'])


@pytest.fixture
def regular_group(seed):
    group = _make_group(seed, False, '__test_immutable_regular__')
    yield group
    _cleanup_group(group['id'])


# ---------------------------------------------------------------------------
# Смена формата запрещена
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_patch_change_true_to_false_400(admin_client, individual_group):
    resp = admin_client.patch(
        f"{BASE_URL}/{individual_group['id']}",
        {'is_individual': False},
        format='json',
    )
    assert resp.status_code == 400
    assert 'error' in resp.json()
    # В БД без изменений.
    assert _db_is_individual(individual_group['id']) is True


@pytest.mark.django_db
def test_patch_change_false_to_true_400(admin_client, regular_group):
    resp = admin_client.patch(
        f"{BASE_URL}/{regular_group['id']}",
        {'is_individual': True},
        format='json',
    )
    assert resp.status_code == 400
    assert _db_is_individual(regular_group['id']) is False


# ---------------------------------------------------------------------------
# Совпадающее значение / отсутствие ключа — no-op
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_patch_same_value_200(admin_client, individual_group):
    """PATCH тем же значением is_individual → 200 (идемпотентно)."""
    resp = admin_client.patch(
        f"{BASE_URL}/{individual_group['id']}",
        {'is_individual': True},
        format='json',
    )
    assert resp.status_code == 200
    assert _db_is_individual(individual_group['id']) is True


@pytest.mark.django_db
def test_patch_without_is_individual_200(admin_client, individual_group):
    """PATCH других полей без is_individual → 200, формат не тронут."""
    resp = admin_client.patch(
        f"{BASE_URL}/{individual_group['id']}",
        {'name': '__test_immutable_renamed__'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['name'] == '__test_immutable_renamed__'
    assert _db_is_individual(individual_group['id']) is True
