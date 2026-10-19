"""
Тесты apps.memberships.services/repository.transfer_membership().

Самодостаточны, по образцу test_individual_group_limit.py: сеют direction_a
(две обычные группы + одна индивидуальная), direction_b (одна группа, для
негативного теста «другое направление»), teacher, двух students.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.groups import repository as groups_repo
from apps.memberships import repository
from apps.memberships.exceptions import (
    DirectionMismatch,
    IndividualGroupFull,
    SameGroupTransfer,
    TargetGroupUnavailable,
)

BASE_URL = '/api/admin/memberships'


@pytest.fixture
def seed():
    """direction_a (group_a1, group_a2, group_a_individual), direction_b (group_b1), teacher, s1/s2."""
    ids: dict[str, int] = {}
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, active) "
            "VALUES ('__tr_dir_a__', true) RETURNING id"
        )
        ids['direction_a'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, active) "
            "VALUES ('__tr_dir_b__', true) RETURNING id"
        )
        ids['direction_b'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO teachers (name, active, created_at) "
            "VALUES ('__tr_teacher__', true, NOW()) RETURNING id"
        )
        ids['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES ('__tr_student_1__', NOW()) RETURNING id"
        )
        ids['s1'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES ('__tr_student_2__', NOW()) RETURNING id"
        )
        ids['s2'] = cur.fetchone()[0]

    def _group(name: str, direction_id: int, is_individual: bool) -> dict:
        return groups_repo.create_group({
            'name': name,
            'direction_id': direction_id,
            'teacher_id': ids['teacher_id'],
            'is_individual': is_individual,
            'lesson_duration_minutes': 90,
            'lessons_per_week': 1,
        })

    group_a1 = _group('__tr_group_a1__', ids['direction_a'], False)
    group_a2 = _group('__tr_group_a2__', ids['direction_a'], False)
    group_a_individual = _group('__tr_group_a_indiv__', ids['direction_a'], True)
    group_b1 = _group('__tr_group_b1__', ids['direction_b'], False)
    ids['group_a1'] = group_a1['id']
    ids['group_a2'] = group_a2['id']
    ids['group_a_individual'] = group_a_individual['id']
    ids['group_b1'] = group_b1['id']

    yield ids

    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM group_memberships WHERE group_id IN (%s, %s, %s, %s)',
            [ids['group_a1'], ids['group_a2'], ids['group_a_individual'], ids['group_b1']],
        )
        cur.execute(
            'DELETE FROM groups WHERE id IN (%s, %s, %s, %s)',
            [ids['group_a1'], ids['group_a2'], ids['group_a_individual'], ids['group_b1']],
        )
        cur.execute('DELETE FROM students WHERE id IN (%s, %s)', [ids['s1'], ids['s2']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [ids['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id IN (%s, %s)', [ids['direction_a'], ids['direction_b']])


# ---------------------------------------------------------------------------
# Repository-level
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTransferMembershipRepository:

    def test_deactivates_old_and_creates_new(self, seed):
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})

        new = repository.transfer_membership(old['id'], seed['group_a2'])

        assert new is not None
        assert new['group_id'] == seed['group_a2']
        assert new['student_id'] == seed['s1']
        assert new['active'] is True

        rows = repository.list_memberships(student_id=seed['s1'], include_inactive=True)
        old_row = next(r for r in rows if r['id'] == old['id'])
        assert old_row['active'] is False

    def test_preserves_old_lessons_done_as_history(self, seed):
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 32,
        })

        repository.transfer_membership(old['id'], seed['group_a2'])

        rows = repository.list_memberships(student_id=seed['s1'], include_inactive=True)
        old_row = next(r for r in rows if r['id'] == old['id'])
        assert float(old_row['lessons_done']) == 32.0

    def test_new_membership_starts_at_zero_lessons(self, seed):
        # group_a2 не должна быть «сольной» целью (иначе сработает Фаза 1b
        # transfer-progress-alignment: apps.memberships.repository.
        # _seed_transfer_continuation засеет lessons_done=B вместо 0 для
        # одинокого ученика в свежей группе) — этот тест проверяет ОБЩЕЕ
        # правило «новая membership стартует с 0», поэтому явно занимаем
        # group_a2 другим активным учеником до перевода.
        repository.add_membership({'group_id': seed['group_a2'], 'student_id': seed['s2']})
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 32,
        })

        new = repository.transfer_membership(old['id'], seed['group_a2'])

        assert float(new['lessons_done']) == 0.0

    def test_sets_transferred_from_link(self, seed):
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 32,
        })

        new = repository.transfer_membership(old['id'], seed['group_a2'])

        assert new['transferred_from_id'] == old['id']
        assert new['transferred_from_group_name'] == '__tr_group_a1__'
        assert float(new['transferred_from_lessons_done']) == 32.0

    def test_nonexistent_membership_returns_none(self, seed):
        result = repository.transfer_membership(999_999_999, seed['group_a2'])
        assert result is None

    def test_inactive_membership_returns_none(self, seed):
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
        repository.remove_membership(old['id'])

        result = repository.transfer_membership(old['id'], seed['group_a2'])
        assert result is None

    def test_same_group_raises(self, seed):
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})

        with pytest.raises(SameGroupTransfer):
            repository.transfer_membership(old['id'], seed['group_a1'])

    def test_different_direction_raises(self, seed):
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})

        with pytest.raises(DirectionMismatch):
            repository.transfer_membership(old['id'], seed['group_b1'])

    def test_target_group_not_found_raises(self, seed):
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})

        with pytest.raises(TargetGroupUnavailable):
            repository.transfer_membership(old['id'], 999_999_999)

    def test_reactivates_existing_target_membership(self, seed):
        # Ученик уже когда-то был в group_a2 (сейчас неактивен там).
        old_in_a2 = repository.add_membership({'group_id': seed['group_a2'], 'student_id': seed['s1']})
        repository.remove_membership(old_in_a2['id'])

        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
        new = repository.transfer_membership(old['id'], seed['group_a2'])

        assert new['id'] == old_in_a2['id']  # тот же id — реактивация, не дубль
        assert new['active'] is True

    def test_individual_group_full_raises(self, seed):
        repository.add_membership({'group_id': seed['group_a_individual'], 'student_id': seed['s2']})
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})

        with pytest.raises(IndividualGroupFull):
            repository.transfer_membership(old['id'], seed['group_a_individual'])


# ---------------------------------------------------------------------------
# API-level
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_transfer_no_cookie_401(anon_client, seed):
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = anon_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_a2']}, format='json')
    assert resp.status_code == 401


@pytest.mark.django_db
def test_transfer_teacher_403(teacher_client, seed):
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = teacher_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_a2']}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_transfer_manager_403(manager_client, seed):
    """Перевод между группами двигает прогресс — остаётся за суперадмином.

    Набор группы (POST/DELETE) менеджеру открыт, transfer и PATCH — нет.
    """
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = manager_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_a2']}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_transfer_admin_403(admin_client, seed):
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = admin_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_a2']}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_transfer_superadmin_200(superadmin_client, seed):
    old = repository.add_membership({
        'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 32,
    })
    resp = superadmin_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_a2']}, format='json')
    assert resp.status_code == 200
    data = resp.json()
    assert data['group_id'] == seed['group_a2']
    assert data['transferred_from_group_name'] == '__tr_group_a1__'
    assert float(data['transferred_from_lessons_done']) == 32.0


@pytest.mark.django_db
def test_transfer_different_direction_400(superadmin_client, seed):
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = superadmin_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_b1']}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_transfer_target_group_not_found_400(superadmin_client, seed):
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = superadmin_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': 999_999_999}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_transfer_same_group_400(superadmin_client, seed):
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = superadmin_client.post(f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_a1']}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_transfer_nonexistent_membership_404(superadmin_client, seed):
    resp = superadmin_client.post(f'{BASE_URL}/999999999/transfer', {'to_group_id': seed['group_a2']}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_transfer_missing_to_group_id_400(superadmin_client, seed):
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
    resp = superadmin_client.post(f"{BASE_URL}/{old['id']}/transfer", {}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_transfer_individual_group_full_409(superadmin_client, seed):
    repository.add_membership({'group_id': seed['group_a_individual'], 'student_id': seed['s2']})
    old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})

    resp = superadmin_client.post(
        f"{BASE_URL}/{old['id']}/transfer", {'to_group_id': seed['group_a_individual']}, format='json',
    )
    assert resp.status_code == 409


@pytest.mark.django_db
def test_transfer_chain_sums_lessons_across_multiple_hops(seed):
    """
    А(20 уроков, seed['group_a1']) → Б(4 урока, seed['group_a2']) → В (новая группа
    того же направления). В В: transferred_from_lessons_done = 20+4=24 (сумма
    по всей цепочке), transferred_from_group_name = имя Б (непосредственный
    источник, не А).

    Проверяется суммирование по ОБЫЧНЫМ (не-продолжающим) хопам, поэтому Фаза 1b
    на промежуточной группе Б явно отключена вторым активным учеником: иначе Б
    стала бы группой-продолжением (offset=20), её lessons_done означал бы
    кумулятив, и ручной update до 4 создал бы состояние, невозможное в проде
    (lessons_done < offset). Обход останавливается на группах-продолжениях —
    см. cumulative_transferred_lessons.
    """
    from apps.groups import repository as groups_repo

    group_a3 = groups_repo.create_group({
        'name': '__tr_group_a3__', 'direction_id': seed['direction_a'], 'teacher_id': seed['teacher_id'],
        'is_individual': False, 'lesson_duration_minutes': 90, 'lessons_per_week': 1,
    })
    repository.add_membership({'group_id': seed['group_a2'], 'student_id': seed['s2']})
    try:
        m_a = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 20,
        })
        m_b = repository.transfer_membership(m_a['id'], seed['group_a2'])
        repository.update_membership(m_b['id'], {'lessons_done': 4})
        m_c = repository.transfer_membership(m_b['id'], group_a3['id'])

        assert float(m_c['transferred_from_lessons_done']) == 24.0
        assert m_c['transferred_from_group_name'] == '__tr_group_a2__'
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_a3['id']])
            cur.execute('DELETE FROM groups WHERE id = %s', [group_a3['id']])


@pytest.mark.django_db
def test_transfer_chain_cycle_does_not_hang(seed):
    """
    А→Б→В→А обратно (реактивация исходной membership А) — цепочка технически
    зацикливается (А.transferred_from → В → Б → А). Функция должна вернуться
    за конечное время, не бросив исключение и не зависнув.

    Каждая из Б/В/А-при-реактивации — «сольная» цель (одна активная membership,
    без проведённых уроков), поэтому без явного отключения сработала бы Фаза 1b
    transfer-progress-alignment (_seed_transfer_continuation засеивает
    lessons_done=B вместо 0), что раздувает суммы cumulative_transferred_lessons
    при проходе через цикл — тестировать это здесь не нужно (Фаза 1b покрыта
    отдельными тестами TestTransferContinuationPhase1b), поэтому явно отключаем
    её на всех трёх шагах: group_a2 — другим активным учеником, group_a_individual
    и group_a1 (перед реактивацией) — проведённым уроком.
    """
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-01-01', 1, 90, 'regular', 'test') RETURNING id",
            [seed['group_a_individual'], seed['teacher_id']],
        )
        lesson_indiv_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-01-01', 1, 90, 'regular', 'test') RETURNING id",
            [seed['group_a1'], seed['teacher_id']],
        )
        lesson_a1_id = cur.fetchone()[0]
    repository.add_membership({'group_id': seed['group_a2'], 'student_id': seed['s2']})
    try:
        m_a = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 10,
        })
        m_b = repository.transfer_membership(m_a['id'], seed['group_a2'])
        m_c = repository.transfer_membership(m_b['id'], seed['group_a_individual'])
        # Назад в А — тот же студент, та же пара (group_a1, s1) реактивируется (id == m_a['id']).
        m_back = repository.transfer_membership(m_c['id'], seed['group_a1'])

        assert m_back['id'] == m_a['id']
        # Обход: В(0) → Б(0) → А(10, уже в seen) → стоп. Сумма = 0+0+10 = 10 —
        # не зависает и не даёт случайное число, цикл детерминированно обрывается
        # на повторном visite id А.
        assert float(m_back['transferred_from_lessons_done']) == 10.0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE id IN (%s, %s)', [lesson_indiv_id, lesson_a1_id])


# ---------------------------------------------------------------------------
# locked_through / locked_through_map
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLockedThrough:

    def test_zero_for_non_transferred_student(self, seed):
        m = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
        assert repository.locked_through(seed['s1'], seed['group_a1']) == Decimal('0')

    def test_equals_cumulative_transferred_for_transferred_student(self, seed):
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 12,
        })
        new = repository.transfer_membership(old['id'], seed['group_a2'])
        assert repository.locked_through(seed['s1'], new['group_id']) == Decimal('12')

    def test_locked_through_map_only_includes_transferred(self, seed):
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 12,
        })
        new = repository.transfer_membership(old['id'], seed['group_a2'])
        repository.add_membership({'group_id': seed['group_a2'], 'student_id': seed['s2']})

        result = repository.locked_through_map(seed['group_a2'], [seed['s1'], seed['s2']])

        assert result == {seed['s1']: Decimal('12')}
