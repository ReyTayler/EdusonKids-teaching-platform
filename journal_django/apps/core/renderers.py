"""
Custom DRF renderer for journal_django.

DateSafeJSONRenderer — safety-net that converts date/datetime objects to ISO strings
                       before JSON serialisation.  Prevents accidental timezone drift
                       if any date objects slip past DateStringField.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from rest_framework.renderers import JSONRenderer


def _js_iso(dt: datetime.datetime) -> str:
    """
    Format a datetime exactly like JavaScript's Date.toISOString():
    UTC, millisecond precision, trailing 'Z'  →  '2026-06-04T08:10:42.355Z'.

    This keeps timestamptz fields byte-identical with the Express/Node API,
    which serialises Date objects via JSON.stringify (→ toISOString()).
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc)
    millis = dt.microsecond // 1000
    return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{millis:03d}Z'


def _coerce_dates(obj):
    """
    Recursively walk obj and convert, to stay byte-identical with the Node/pg API:
      datetime.date (not datetime.datetime) → 'YYYY-MM-DD'
      datetime.datetime                     → JS toISOString() format ('...Z')
      Decimal (PG numeric)                  → string preserving DB scale ('6290.00')

    node-postgres returns `numeric` columns as strings (to avoid float precision
    loss); psycopg2 returns them as Decimal with the scale preserved, so str()
    reproduces the exact same wire value ('6290.00', '0.1500', ...).
    """
    if isinstance(obj, datetime.datetime):
        return _js_iso(obj)
    if isinstance(obj, datetime.date):
        return obj.strftime('%Y-%m-%d')
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _coerce_dates(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_coerce_dates(item) for item in obj]
    return obj


class DateSafeJSONRenderer(JSONRenderer):
    """
    JSONRenderer subclass that walks the response data and converts any
    date/datetime Python objects to strings before serialisation.

    Order of precedence:
      datetime.datetime checked first (it subclasses datetime.date).
      datetime.date → 'YYYY-MM-DD' string.
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        safe_data = _coerce_dates(data)
        return super().render(safe_data, accepted_media_type, renderer_context)
