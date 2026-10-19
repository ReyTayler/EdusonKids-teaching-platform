"""
Unit/integration тесты для MembershipsRepository.

Используют реальную БД (managed=False, продовая).
Все созданные строки удаляются в teardown.

Тестируют:
  - list_memberships: форма ответа (список, не пагинатор)
  - list_memberships: фильтр group_id/student_id
  - list_memberships: include_inactive=False (по умолчанию) и True
  - add_membership: создаёт запись
  - add_membership: UPSERT — повторный POST той же пары → реактивация, не дубль
  - update_membership: COALESCE-обновление
  - update_membership: несуществующий ID → None
  - remove_membership: active=false, повторный → False
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.core.tests.minimal_school import ensure_minimal_school
from apps.memberships import repository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_valid_group_id() -> int:
    """Активная группа минимальной школы (в ней уже есть одно активное членство)."""
    return ensure_minimal_school()['group_id']


def _get_valid_student_id() -> int:
    """Ученик без членства: тесты записи сами создают пару «группа + ученик» (ждут 201)."""
    return ensure_minimal_school()['free_student_id']


def _create_test_membership(group_id: int, student_id: int, **overrides) -> dict:
    """Вставить тестовую запись в group_memberships напрямую, возвращает dict."""
    data = {
        'group_id': group_id,
        'student_id': student_id,
        'lessons_done': 0,
        'start_date': None,
        'sheet_row': None,
        **overrides,
    }
    return repository.add_membership(data)


def _cleanup_membership(membership_id: int) -> None:
    """Прямой DELETE тестовой строки."""
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def _cleanup_membership_by_pair(group_id: int, student_id: int) -> None:
    """Удалить все строки для пары (group_id, student_id)."""
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM group_memberships WHERE group_id = %s AND student_id = %s',
            [group_id, student_id],
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestListMemberships:
    """Тесты list_memberships()."""

    def test_returns_list(self):
        result = repository.list_memberships()
        assert isinstance(result, list)

    def test_not_paginated(self):
        """Результат — список, не dict с {rows, total, page, page_size}."""
        result = repository.list_memberships()
        assert not isinstance(result, dict)

    def test_default_only_active(self):
        """По умолчанию include_inactive=False — только active=true."""
        result = repository.list_memberships()
        for row in result:
            assert row['active'] is True

    def test_include_inactive_includes_all(self):
        """include_inactive=True — возвращает все записи включая неактивные."""
        all_result = repository.list_memberships(include_inactive=True)
        active_result = repository.list_memberships(include_inactive=False)
        # С include_inactive >= без include_inactive
        assert len(all_result) >= len(active_result)

    def test_rows_have_group_name(self):
        """Каждая строка содержит group_name из JOIN."""
        result = repository.list_memberships()
        if result:
            assert 'group_name' in result[0]
            assert result[0]['group_name'] is not None

    def test_rows_have_student_name(self):
        """Каждая строка содержит student_name (full_name) из JOIN."""
        result = repository.list_memberships()
        if result:
            assert 'student_name' in result[0]
            assert result[0]['student_name'] is not None

    def test_filter_by_group_id(self):
        """Фильтр group_id возвращает только записи этой группы."""
        group_id = _get_valid_group_id()
        result = repository.list_memberships(group_id=group_id, include_inactive=True)
        assert result
        for row in result:
            assert row['group_id'] == group_id

    def test_filter_by_student_id(self):
        """Фильтр student_id возвращает только записи этого ученика."""
        student_id = ensure_minimal_school()['student_id']  # ученик с членством
        result = repository.list_memberships(student_id=student_id, include_inactive=True)
        assert result
        for row in result:
            assert row['student_id'] == student_id

    def test_filter_nonexistent_group_returns_empty(self):
        result = repository.list_memberships(group_id=999_999_999)
        assert result == []

    def test_filter_nonexistent_student_returns_empty(self):
        result = repository.list_memberships(student_id=999_999_999)
        assert result == []

    def test_rows_have_computed_remaining(self):
        """remaining — вычисляемое (общий баланс ученика), не хранимая колонка."""
        result = repository.list_memberships()
        if result:
            assert 'remaining' in result[0]
            assert isinstance(result[0]['remaining'], (int, float))


@pytest.mark.django_db
class TestAddMembership:
    """Тесты add_membership() — create и UPSERT."""

    def test_add_returns_dict(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        try:
            m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
            assert isinstance(m, dict)
            assert 'id' in m
            assert m['group_id'] == group_id
            assert m['student_id'] == student_id
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_add_active_true_by_default(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        try:
            m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
            assert m['active'] is True
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_add_with_start_date(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        try:
            m = repository.add_membership({
                'group_id': group_id,
                'student_id': student_id,
                'start_date': '2025-09-01',
            })
            assert m['start_date'][:10] == '2025-09-01'
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_upsert_reactivates_existing(self):
        """
        UPSERT: повторный вызов той же пары (group_id, student_id) → реактивация.

        Сценарий: создаём → soft-delete → повторный POST → active=true, не дубль.
        """
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        try:
            # Создаём
            m1 = repository.add_membership({'group_id': group_id, 'student_id': student_id})
            membership_id = m1['id']
            # Деактивируем
            repository.remove_membership(membership_id)
            # Повторный POST → реактивация
            m2 = repository.add_membership({'group_id': group_id, 'student_id': student_id})
            # Тот же id (ON CONFLICT DO UPDATE)
            assert m2['id'] == membership_id
            assert m2['active'] is True
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_upsert_no_duplicate(self):
        """
        После UPSERT в БД должна быть ровно одна строка для пары.
        """
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        try:
            repository.add_membership({'group_id': group_id, 'student_id': student_id})
            repository.add_membership({'group_id': group_id, 'student_id': student_id})
            with connection.cursor() as cur:
                cur.execute(
                    'SELECT COUNT(*) FROM group_memberships WHERE group_id = %s AND student_id = %s',
                    [group_id, student_id],
                )
                count = cur.fetchone()[0]
            assert count == 1
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_add_includes_computed_remaining(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        try:
            m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
            assert 'remaining' in m
            assert isinstance(m['remaining'], (int, float))
        finally:
            _cleanup_membership_by_pair(group_id, student_id)


@pytest.mark.django_db
class TestUpdateMembership:
    """Тесты update_membership()."""

    def test_update_nonexistent_returns_none(self):
        result = repository.update_membership(999_999_999, {'lessons_done': 5})
        assert result is None

    def test_update_active_false(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
        try:
            updated = repository.update_membership(m['id'], {'active': False})
            assert updated is not None
            assert updated['active'] is False
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_update_active_can_be_false(self):
        """active=False корректно передаётся (не путается с None sentinel)."""
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
        try:
            updated = repository.update_membership(m['id'], {'active': False})
            assert updated['active'] is False
            # Реактивация
            updated2 = repository.update_membership(m['id'], {'active': True})
            assert updated2['active'] is True
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_update_start_date(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
        try:
            updated = repository.update_membership(m['id'], {'start_date': '2025-01-15'})
            assert updated is not None
            assert updated['start_date'][:10] == '2025-01-15'
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_update_coalesce_preserves_unset_fields(self):
        """COALESCE: незаданные поля сохраняют старые значения."""
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        m = repository.add_membership({
            'group_id': group_id,
            'student_id': student_id,
            'start_date': '2025-03-01',
        })
        try:
            # Обновляем только active, start_date должна остаться
            updated = repository.update_membership(m['id'], {'active': True})
            assert updated is not None
            assert updated['start_date'][:10] == '2025-03-01'
        finally:
            _cleanup_membership_by_pair(group_id, student_id)


@pytest.mark.django_db
class TestRemoveMembership:
    """Тесты remove_membership()."""

    def test_remove_existing_returns_true(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
        try:
            result = repository.remove_membership(m['id'])
            assert result is True
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_remove_sets_active_false(self):
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
        try:
            repository.remove_membership(m['id'])
            # Проверяем через list с include_inactive
            result = repository.list_memberships(
                group_id=group_id, student_id=student_id, include_inactive=True
            )
            assert len(result) == 1
            assert result[0]['active'] is False
        finally:
            _cleanup_membership_by_pair(group_id, student_id)

    def test_remove_nonexistent_returns_false(self):
        result = repository.remove_membership(999_999_999)
        assert result is False

    def test_remove_already_removed_returns_true_if_exists(self):
        """
        Повторный remove на уже soft-deleted строку — строка существует,
        active уже false, rowcount=1 → True (строка была найдена).
        """
        group_id = _get_valid_group_id()
        student_id = _get_valid_student_id()
        _cleanup_membership_by_pair(group_id, student_id)
        m = repository.add_membership({'group_id': group_id, 'student_id': student_id})
        try:
            repository.remove_membership(m['id'])
            # Повторный вызов — строка есть, active уже false, rowcount = 1
            result2 = repository.remove_membership(m['id'])
            assert result2 is True  # строка была найдена (rowcount > 0)
        finally:
            _cleanup_membership_by_pair(group_id, student_id)
