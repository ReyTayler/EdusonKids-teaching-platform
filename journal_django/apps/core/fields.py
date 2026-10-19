"""
Custom DRF serializer fields for journal_django.

DateStringField — handles DATE values that arrive from PostgreSQL as plain strings
                  (due to the type-parser in services/db.js / psycopg2 cursor).
                  Prevents timezone-induced date drift on Europe/Moscow offsets.
"""
from __future__ import annotations

import datetime
from typing import Optional

from rest_framework import serializers


class DateStringField(serializers.Field):
    """
    Serializer field for DATE columns that must stay as 'YYYY-MM-DD' strings.

    to_representation:
        date  object  → 'YYYY-MM-DD'
        str           → first 10 characters (already 'YYYY-MM-DD' from DB)
        None / falsy  → None

    to_internal_value:
        str 'YYYY-MM-DD' → str 'YYYY-MM-DD'  (returned as string, NOT as date object)
        anything else    → ValidationError

    Design note: we deliberately return str (not date) from to_internal_value so that
    the value is safe to pass directly to raw SQL without Django auto-converting it
    back to a datetime and shifting the timezone.
    """

    def to_representation(self, value) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
            return value.strftime('%Y-%m-%d')
        if isinstance(value, str):
            return value[:10]
        # datetime.datetime — return date portion only
        if isinstance(value, datetime.datetime):
            return value.date().strftime('%Y-%m-%d')
        return str(value)[:10]

    def to_internal_value(self, data) -> str:
        if not isinstance(data, str):
            raise serializers.ValidationError('Date must be a string in YYYY-MM-DD format.')
        try:
            datetime.date.fromisoformat(data)
        except ValueError:
            raise serializers.ValidationError(
                f"Invalid date '{data}'. Expected YYYY-MM-DD format."
            )
        return data
