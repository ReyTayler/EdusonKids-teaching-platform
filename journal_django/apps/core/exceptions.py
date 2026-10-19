"""
Custom exception handler for journal_django.

Mirrors the Express error response format:
  { "error": "..." }                              — most errors
  { "error": "Validation failed", "details": {} } — DRF ValidationError

DRF default handler returns:
  { "field": ["msg"] }   for validation errors
  { "detail": "msg" }    for auth/permission/not-found errors

We remap both to the Express-compatible shape.
"""
from __future__ import annotations

from typing import Optional

from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler


def custom_exception_handler(exc, context) -> Optional[Response]:
    """
    Custom exception handler registered in REST_FRAMEWORK settings.

    Processing order:
    1. ValidationError  → 400 {"error": "Validation failed", "details": <field errors>}
    2. NotAuthenticated → 401 {"error": "Unauthorized"}
    3. AuthenticationFailed → 401 {"error": <message>}
    4. PermissionDenied → 403 {"error": "Forbidden"}
    5. NotFound         → 404 {"error": "Not found"}
    6. Other HttpException → pass through with {"error": <detail string>}
    7. Unhandled        → None (Django will 500)
    """
    # Let DRF build its default response first so we get the correct HTTP status
    response = drf_default_handler(exc, context)

    if response is None:
        # Unhandled exception — Django will return 500
        return None

    if isinstance(exc, ValidationError):
        return Response(
            {'error': 'Validation failed', 'details': exc.detail},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, NotAuthenticated):
        return Response(
            {'error': 'Unauthorized'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, AuthenticationFailed):
        detail = _extract_detail(exc)
        return Response(
            {'error': detail},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, PermissionDenied):
        return Response(
            {'error': 'Forbidden'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, NotFound):
        return Response(
            {'error': 'Not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Generic HttpException — wrap detail in error key
    detail = _extract_detail(exc)
    return Response(
        {'error': detail},
        status=response.status_code,
    )


def _extract_detail(exc) -> str:
    """Extract a plain string message from a DRF exception."""
    detail = getattr(exc, 'detail', str(exc))
    if isinstance(detail, list):
        return str(detail[0]) if detail else str(exc)
    if isinstance(detail, dict):
        # Take first field's first message
        first_key = next(iter(detail), None)
        if first_key:
            val = detail[first_key]
            return str(val[0]) if isinstance(val, list) else str(val)
    return str(detail)
