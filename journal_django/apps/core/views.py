"""
Core views for journal_django.

GET /health — database connectivity probe.
Response: {"status": "ok", "db": "ok"|"error"}
"""
from __future__ import annotations

from django.db import connection, OperationalError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """
    Lightweight health-check endpoint.

    No authentication required.  Performs a SELECT 1 against the
    PostgreSQL database and reports the result.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        db_status = 'ok'
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
        except (OperationalError, Exception):
            db_status = 'error'

        return Response({'status': 'ok', 'db': db_status})
