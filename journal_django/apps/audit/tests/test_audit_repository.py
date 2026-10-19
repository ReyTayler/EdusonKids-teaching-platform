"""
Unit-тесты для AuditRepository.
"""
from __future__ import annotations

import pytest

from apps.audit import repository


@pytest.mark.django_db
def test_list_audit_returns_paginated_shape():
    result = repository.list_audit()
    assert isinstance(result, dict)
    assert 'rows' in result
    assert 'total' in result
    assert 'page' in result
    assert 'page_size' in result
    assert isinstance(result['rows'], list)
    assert isinstance(result['total'], int)


@pytest.mark.django_db
def test_list_audit_default_sort_is_occurred_at_desc():
    """По умолчанию сортировка occurred_at DESC — не падает."""
    result = repository.list_audit(sort_by='occurred_at', sort_dir='desc')
    assert result['page'] == 1


@pytest.mark.django_db
def test_list_audit_page_size():
    result = repository.list_audit(page=1, page_size=5)
    assert result['page_size'] == 5
    assert len(result['rows']) <= 5


@pytest.mark.django_db
def test_list_audit_event_filter():
    """Фильтр по event (exact) не ломает запрос."""
    result = repository.list_audit(filters={'event': '__nonexistent_event__xyz__'})
    assert result['rows'] == []
    assert result['total'] == 0


@pytest.mark.django_db
def test_list_audit_sort_by_event():
    result = repository.list_audit(sort_by='event', sort_dir='asc')
    assert isinstance(result['rows'], list)
