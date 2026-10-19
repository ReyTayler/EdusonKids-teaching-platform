"""
Tests for DateStringField.

Verifies that:
- '2026-06-09' passes through unchanged (no timezone drift)
- date objects are serialised as 'YYYY-MM-DD'
- None is returned for None input
- Invalid strings raise ValidationError
- Non-string input raises ValidationError

No database access required.
"""
from __future__ import annotations

import datetime

import pytest
from rest_framework.exceptions import ValidationError

from apps.core.fields import DateStringField


class TestDateStringFieldToRepresentation:
    """Tests for the serialisation direction (model → JSON)."""

    def setup_method(self):
        self.field = DateStringField()

    def test_string_passthrough(self):
        """A plain 'YYYY-MM-DD' string must pass through as-is."""
        assert self.field.to_representation('2026-06-09') == '2026-06-09'

    def test_string_with_time_truncated(self):
        """A string with time portion should return only the first 10 chars."""
        assert self.field.to_representation('2026-06-09T12:00:00') == '2026-06-09'

    def test_date_object(self):
        """A datetime.date object is formatted as 'YYYY-MM-DD'."""
        d = datetime.date(2026, 6, 9)
        assert self.field.to_representation(d) == '2026-06-09'

    def test_datetime_object_returns_date_only(self):
        """A datetime.datetime object returns only the date portion."""
        dt = datetime.datetime(2026, 6, 9, 23, 0, 0)
        assert self.field.to_representation(dt) == '2026-06-09'

    def test_none_returns_none(self):
        """None input must return None (nullable date column)."""
        assert self.field.to_representation(None) is None

    def test_no_date_drift_for_edge_midnight(self):
        """
        Key invariant: '2026-06-09' must never become '2026-06-08' or '2026-06-10'.
        This is the Moscow-timezone drift protection test.
        """
        value = '2026-06-09'
        result = self.field.to_representation(value)
        assert result == '2026-06-09', (
            f"Date drift detected! Expected '2026-06-09', got '{result}'"
        )


class TestDateStringFieldToInternalValue:
    """Tests for the deserialisation direction (JSON → model)."""

    def setup_method(self):
        self.field = DateStringField()

    def test_valid_date_string_returns_string(self):
        """Valid 'YYYY-MM-DD' string must be returned as a string (not a date object)."""
        result = self.field.to_internal_value('2026-06-09')
        assert result == '2026-06-09'
        assert isinstance(result, str), 'to_internal_value must return str, not date object'

    def test_invalid_format_raises_validation_error(self):
        """Non-ISO date strings must raise ValidationError."""
        with pytest.raises(ValidationError):
            self.field.to_internal_value('09/06/2026')

    def test_invalid_date_values_raise_validation_error(self):
        """Syntactically valid but impossible dates must raise ValidationError."""
        with pytest.raises(ValidationError):
            self.field.to_internal_value('2026-13-01')  # month 13 does not exist

    def test_non_string_raises_validation_error(self):
        """Non-string input (e.g. a date object) must raise ValidationError."""
        with pytest.raises(ValidationError):
            self.field.to_internal_value(datetime.date(2026, 6, 9))

    def test_empty_string_raises_validation_error(self):
        """Empty string is not a valid date."""
        with pytest.raises(ValidationError):
            self.field.to_internal_value('')

    def test_roundtrip_identity(self):
        """to_internal_value → to_representation must be identity for valid dates."""
        date_str = '2026-01-31'
        internal = self.field.to_internal_value(date_str)
        output = self.field.to_representation(internal)
        assert output == date_str
