"""
Тесты repository.place_student_in_group — движка перевода/записи, на который
опирается legacy-эндпоинт POST /api/admin/memberships/:id/transfer. Свой HTTP-вход
(POST /memberships/place) у движка был и снят, поэтому тесты здесь только на
уровне repository.

Переиспользует seed-фикстуру из test_transfer_membership: direction_a (две обычные
группы + одна индивидуальная), direction_b (одна группа), teacher, s1/s2.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.memberships import repository
from apps.memberships.exceptions import (
    AlreadyActiveInGroup,
    DirectionMismatch,
    IndividualGroupFull,
    SameGroupTransfer,
    SourceMembershipInvalid,
    TargetGroupUnavailable,
)

# Фикстура seed определена в test_transfer_membership.py; переиспользуем через импорт.
from apps.memberships.tests.test_transfer_membership import seed  # noqa: F401


# ---------------------------------------------------------------------------
# Repository / service уровень
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPlaceStudentRepository:

    def test_transfer_from_active_deactivates_and_links(self, seed):
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 12,
        })

        new = repository.place_student_in_group(
            seed['s1'], seed['group_a2'], from_membership_id=old['id'],
        )

        assert new['group_id'] == seed['group_a2']
        assert new['active'] is True
        assert new['transferred_from_id'] == old['id']
        assert float(new['transferred_from_lessons_done']) == 12.0
        # старая деактивирована, lessons_done сохранён как история
        rows = repository.list_memberships(student_id=seed['s1'], include_inactive=True)
        old_row = next(r for r in rows if r['id'] == old['id'])
        assert old_row['active'] is False
        assert float(old_row['lessons_done']) == 12.0

    def test_record_with_history_from_inactive_source(self, seed):
        # Ученик когда-то был в group_a1 (12 уроков), потом ушёл (неактивен).
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 12,
        })
        repository.remove_membership(old['id'])

        # Возвращается — записываем в group_a2 того же направления, тянем историю.
        new = repository.place_student_in_group(
            seed['s1'], seed['group_a2'], from_membership_id=old['id'],
        )

        assert new['active'] is True
        assert new['transferred_from_id'] == old['id']
        assert float(new['transferred_from_lessons_done']) == 12.0
        # Источник НЕ трогаем — он и так неактивен.
        rows = repository.list_memberships(student_id=seed['s1'], include_inactive=True)
        old_row = next(r for r in rows if r['id'] == old['id'])
        assert old_row['active'] is False

    def test_fresh_enrollment_no_source(self, seed):
        new = repository.place_student_in_group(seed['s1'], seed['group_a1'])

        assert new['active'] is True
        assert new['transferred_from_id'] is None
        assert float(new['lessons_done']) == 0.0

    def test_fresh_enrollment_into_other_direction(self, seed):
        # from=None → правило «то же направление» не действует, запись в любое направление.
        new = repository.place_student_in_group(seed['s1'], seed['group_b1'])
        assert new['group_id'] == seed['group_b1']
        assert new['transferred_from_id'] is None

    def test_already_active_in_target_raises(self, seed):
        repository.add_membership({'group_id': seed['group_a2'], 'student_id': seed['s1']})
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})

        with pytest.raises(AlreadyActiveInGroup):
            repository.place_student_in_group(
                seed['s1'], seed['group_a2'], from_membership_id=old['id'],
            )

    def test_same_group_raises(self, seed):
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
        with pytest.raises(SameGroupTransfer):
            repository.place_student_in_group(
                seed['s1'], seed['group_a1'], from_membership_id=old['id'],
            )

    def test_different_direction_source_raises(self, seed):
        old = repository.add_membership({'group_id': seed['group_b1'], 'student_id': seed['s1']})
        with pytest.raises(DirectionMismatch):
            repository.place_student_in_group(
                seed['s1'], seed['group_a1'], from_membership_id=old['id'],
            )

    def test_source_of_other_student_raises(self, seed):
        other = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s2']})
        with pytest.raises(SourceMembershipInvalid):
            repository.place_student_in_group(
                seed['s1'], seed['group_a2'], from_membership_id=other['id'],
            )

    def test_source_not_found_raises(self, seed):
        with pytest.raises(SourceMembershipInvalid):
            repository.place_student_in_group(
                seed['s1'], seed['group_a2'], from_membership_id=999_999_999,
            )

    def test_target_group_not_found_raises(self, seed):
        with pytest.raises(TargetGroupUnavailable):
            repository.place_student_in_group(seed['s1'], 999_999_999)

    def test_individual_group_full_raises(self, seed):
        repository.add_membership({'group_id': seed['group_a_individual'], 'student_id': seed['s2']})
        with pytest.raises(IndividualGroupFull):
            repository.place_student_in_group(seed['s1'], seed['group_a_individual'])

    def test_transfer_wrapper_still_delegates(self, seed):
        """Legacy transfer_membership по-прежнему работает через place_student_in_group."""
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 5,
        })
        new = repository.transfer_membership(old['id'], seed['group_a2'])
        assert new['group_id'] == seed['group_a2']
        assert new['transferred_from_id'] == old['id']

    def test_transfer_wrapper_inactive_returns_none(self, seed):
        old = repository.add_membership({'group_id': seed['group_a1'], 'student_id': seed['s1']})
        repository.remove_membership(old['id'])
        assert repository.transfer_membership(old['id'], seed['group_a2']) is None


# ---------------------------------------------------------------------------
# Phase 1b: transfer continuation seeding (lesson_number_offset)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTransferContinuationPhase1b:

    def test_seeds_lessons_done_and_offset_for_solo_new_group(self, seed):
        """Ученик с B=20 переводится в СВЕЖУЮ группу (0 уроков, будет один) —
        новая membership стартует с lessons_done=20, группа получает offset=20."""
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 20,
        })
        new = repository.place_student_in_group(
            seed['s1'], seed['group_a2'], from_membership_id=old['id'],
        )
        assert float(new['lessons_done']) == 20.0

        from apps.groups.models import Group
        offset = Group.objects.filter(id=seed['group_a2']).values_list(
            'lesson_number_offset', flat=True,
        ).first()
        assert offset == Decimal('20.0')

    def test_no_seed_when_group_has_other_active_member(self, seed):
        """group_a2 уже занят s2 — s1 переводится туда же (не индивидуальная группа,
        значит это допустимо), continuation НЕ применяется (не «сольная» группа)."""
        repository.add_membership({'group_id': seed['group_a2'], 'student_id': seed['s2']})
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 20,
        })
        new = repository.place_student_in_group(
            seed['s1'], seed['group_a2'], from_membership_id=old['id'],
        )
        assert float(new['lessons_done']) == 0.0

        from apps.groups.models import Group
        offset = Group.objects.filter(id=seed['group_a2']).values_list(
            'lesson_number_offset', flat=True,
        ).first()
        assert offset == Decimal('0.0')

    def test_no_seed_for_fresh_enrollment_without_source(self, seed):
        new = repository.place_student_in_group(seed['s1'], seed['group_a2'])
        assert float(new['lessons_done']) == 0.0

    def test_no_seed_when_source_has_zero_lessons(self, seed):
        """from_membership_id задан, но B=0 (источник с lessons_done=0) —
        continuation не применяется, новая membership стартует с 0."""
        old = repository.add_membership({
            'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 0,
        })
        new = repository.place_student_in_group(
            seed['s1'], seed['group_a2'], from_membership_id=old['id'],
        )
        assert float(new['lessons_done']) == 0.0

        from apps.groups.models import Group
        offset = Group.objects.filter(id=seed['group_a2']).values_list(
            'lesson_number_offset', flat=True,
        ).first()
        assert offset == Decimal('0.0')

    def test_second_consecutive_continuation_does_not_double_count(self, seed):
        """Регрессия: А(18) → Б(продолжение) → В — в В должно приехать 18, НЕ 36.

        Баг: lessons_done группы-продолжения Б засеян кумулятивом (18), и обход
        цепочки складывал его с lessons_done предка А (ещё 18) → offset(В)=36,
        курс в В стартовал с урока №37, перепрыгнув 18 уроков программы.
        """
        from apps.groups import repository as groups_repo
        from apps.groups.models import Group

        group_c = groups_repo.create_group({
            'name': '__cont_group_c__', 'direction_id': seed['direction_a'],
            'teacher_id': seed['teacher_id'], 'is_individual': False,
            'lesson_duration_minutes': 90, 'lessons_per_week': 1,
        })
        try:
            m_a = repository.add_membership({
                'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 18,
            })
            # А → Б: сольная пустая группа, Фаза 1b засеивает offset=18.
            m_b = repository.place_student_in_group(
                seed['s1'], seed['group_a2'], from_membership_id=m_a['id'],
            )
            assert float(m_b['lessons_done']) == 18.0
            assert Group.objects.filter(id=seed['group_a2']).values_list(
                'lesson_number_offset', flat=True,
            ).first() == Decimal('18.0')

            # Б → В: ученик по-прежнему прошёл 18 уроков, а не 36.
            m_c = repository.place_student_in_group(
                seed['s1'], group_c['id'], from_membership_id=m_b['id'],
            )
            assert float(m_c['lessons_done']) == 18.0
            assert Group.objects.filter(id=group_c['id']).values_list(
                'lesson_number_offset', flat=True,
            ).first() == Decimal('18.0')
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_c['id']])
                cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_c['id']])
                cur.execute('DELETE FROM groups WHERE id = %s', [group_c['id']])

    def test_no_seed_when_target_group_already_has_regular_lesson(self, seed):
        """group_a2 уже вело курс (есть regular-урок) — offset не переписываем."""
        from apps.lessons import services as lessons_services
        lesson = lessons_services.create_lesson_full({
            'lesson_date': '2026-03-01', 'group_id': seed['group_a2'],
            'teacher_id': seed['teacher_id'], 'lesson_number': 1,
            'lesson_duration_minutes': 90,
        })
        try:
            old = repository.add_membership({
                'group_id': seed['group_a1'], 'student_id': seed['s1'], 'lessons_done': 20,
            })
            new = repository.place_student_in_group(
                seed['s1'], seed['group_a2'], from_membership_id=old['id'],
            )
            assert float(new['lessons_done']) == 0.0
        finally:
            # seed-фикстура (test_transfer_membership.py) удаляет группы в teardown
            # без учёта FK от lessons/payroll — подчищаем через delete_lesson_full
            # (снимает и Payroll), чтобы не ловить ForeignKeyViolation при удалении group_a2.
            lessons_services.delete_lesson_full(lesson['lesson_id'])

