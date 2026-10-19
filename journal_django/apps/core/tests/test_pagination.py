"""
Tests for WhitelistOrderingFilter and build_raw_order_clause.

No database access required.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import ValidationError

from apps.core.pagination import WhitelistOrderingFilter, build_raw_order_clause


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(sort_by: str | None = None, sort_dir: str | None = None) -> MagicMock:
    """Build a minimal mock DRF Request with query_params."""
    mock_request = MagicMock()
    params: dict = {}
    if sort_by is not None:
        params['sort_by'] = sort_by
    if sort_dir is not None:
        params['sort_dir'] = sort_dir
    mock_request.query_params = params
    return mock_request


def _make_view(ordering_fields: list, ordering: str = 'name') -> MagicMock:
    """Build a minimal mock APIView."""
    view = MagicMock()
    view.ordering_fields = ordering_fields
    view.ordering = ordering
    return view


# ---------------------------------------------------------------------------
# WhitelistOrderingFilter tests
# ---------------------------------------------------------------------------

class TestWhitelistOrderingFilter:
    """Tests for WhitelistOrderingFilter.get_ordering()."""

    def test_valid_field_asc(self):
        """A whitelisted sort_by with sort_dir=asc returns [field]."""
        f = WhitelistOrderingFilter()
        request = _make_request(sort_by='name', sort_dir='asc')
        view = _make_view(['name', 'created_at'])

        ordering = f.get_ordering(request, queryset=None, view=view)
        assert ordering == ['name']

    def test_valid_field_desc(self):
        """A whitelisted sort_by with sort_dir=desc returns ['-field']."""
        f = WhitelistOrderingFilter()
        request = _make_request(sort_by='created_at', sort_dir='desc')
        view = _make_view(['name', 'created_at'])

        ordering = f.get_ordering(request, queryset=None, view=view)
        assert ordering == ['-created_at']

    def test_invalid_sort_by_raises_validation_error(self):
        """A sort_by not in whitelist must raise ValidationError."""
        f = WhitelistOrderingFilter()
        request = _make_request(sort_by='password', sort_dir='asc')
        view = _make_view(['name', 'created_at'])

        with pytest.raises(ValidationError):
            f.get_ordering(request, queryset=None, view=view)

    def test_invalid_sort_dir_raises_validation_error(self):
        """sort_dir other than 'asc' or 'desc' must raise ValidationError."""
        f = WhitelistOrderingFilter()
        request = _make_request(sort_by='name', sort_dir='random')
        view = _make_view(['name', 'created_at'])

        with pytest.raises(ValidationError):
            f.get_ordering(request, queryset=None, view=view)

    def test_default_ordering_used_when_no_params(self):
        """When no sort_by is provided, view.ordering default is used."""
        f = WhitelistOrderingFilter()
        request = _make_request()  # no params
        view = _make_view(['name', 'created_at'], ordering='name')

        ordering = f.get_ordering(request, queryset=None, view=view)
        assert ordering == ['name']

    def test_default_desc_ordering(self):
        """view.ordering='-created_at' should yield ['-created_at'] by default."""
        f = WhitelistOrderingFilter()
        request = _make_request()
        view = _make_view(['name', 'created_at'], ordering='-created_at')

        ordering = f.get_ordering(request, queryset=None, view=view)
        assert ordering == ['-created_at']

    def test_sort_dir_asc_overrides_desc_default(self):
        """Explicit sort_dir=asc overrides a descending default."""
        f = WhitelistOrderingFilter()
        request = _make_request(sort_dir='asc')
        view = _make_view(['name', 'created_at'], ordering='-created_at')

        ordering = f.get_ordering(request, queryset=None, view=view)
        # field resolves to default 'created_at', direction forced to asc
        assert ordering == ['created_at']


# ---------------------------------------------------------------------------
# build_raw_order_clause tests
# ---------------------------------------------------------------------------

class TestBuildRawOrderClause:
    """Tests for the raw-SQL ORDER BY helper."""

    def test_valid_field_asc(self):
        clause = build_raw_order_clause('name', 'asc', ['name', 'age'], 'name')
        assert clause == '"name" ASC'

    def test_valid_field_desc(self):
        clause = build_raw_order_clause('age', 'desc', ['name', 'age'], 'name')
        assert clause == '"age" DESC'

    def test_default_used_when_sort_by_none(self):
        clause = build_raw_order_clause(None, None, ['name', 'age'], 'name')
        assert clause == '"name" ASC'

    def test_invalid_sort_by_raises(self):
        with pytest.raises(ValidationError):
            build_raw_order_clause('secret', 'asc', ['name', 'age'], 'name')

    def test_invalid_sort_dir_raises(self):
        with pytest.raises(ValidationError):
            build_raw_order_clause('name', 'sideways', ['name', 'age'], 'name')
