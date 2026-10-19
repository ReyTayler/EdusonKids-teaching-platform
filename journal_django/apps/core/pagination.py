"""
Pagination and ordering utilities for journal_django.

StandardPagination   — DRF PageNumberPagination; response shape matches Express paginator
                       (services/pagination.js) EXACTLY: {rows, total, page, page_size}

WhitelistOrderingFilter — DRF OrderingFilter replacement that:
                          * uses 'sort_by' / 'sort_dir' query params (matches Express API)
                          * validates sort_by against view.ordering_fields whitelist
                          * validates sort_dir is 'asc' or 'desc'
                          * raises ValidationError on invalid values (never silently ignores)

build_raw_order_clause  — helper for raw-SQL queries
"""
from __future__ import annotations

from typing import Optional

from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class StandardPagination(PageNumberPagination):
    """
    Page-number paginator.

    Query params: page (1-based), page_size (default 50, max 500).
    Response envelope: {rows, total, page, page_size} — matches services/pagination.js.
    """

    page_size = 50
    max_page_size = 500
    page_size_query_param = 'page_size'
    page_query_param = 'page'

    def get_paginated_response(self, data: list) -> Response:
        return Response({
            'rows': data,
            'total': self.page.paginator.count,
            'page': self.page.number,
            'page_size': self.get_page_size(self.request),
        })

    def get_paginated_response_schema(self, schema: dict) -> dict:
        return {
            'type': 'object',
            'properties': {
                'rows': schema,
                'total': {'type': 'integer'},
                'page': {'type': 'integer'},
                'page_size': {'type': 'integer'},
            },
        }


class WhitelistOrderingFilter(OrderingFilter):
    """
    Ordering filter that uses 'sort_by' / 'sort_dir' query params.

    Rules:
    - sort_by must be in view.ordering_fields (list/tuple of allowed column names).
      If not present or invalid → ValidationError.
    - sort_dir must be 'asc' or 'desc'.  If absent → view.ordering (default) is used.
      If present but invalid → ValidationError.

    Attach to a view:
        filter_backends = [WhitelistOrderingFilter]
        ordering_fields = ['name', 'created_at']
        ordering = 'name'   # default sort_by value
    """

    ordering_param = 'sort_by'

    def get_ordering(self, request: Request, queryset, view: APIView):
        sort_by: Optional[str] = request.query_params.get('sort_by')
        sort_dir: Optional[str] = request.query_params.get('sort_dir')

        # Resolve allowed fields from view
        whitelist: list[str] = list(getattr(view, 'ordering_fields', []))
        default_ordering: str = str(getattr(view, 'ordering', whitelist[0] if whitelist else 'id'))

        # Validate sort_by
        if sort_by is not None:
            if sort_by not in whitelist:
                raise ValidationError(
                    f"Invalid sort_by '{sort_by}'. Allowed: {whitelist}"
                )
            field = sort_by
        else:
            # Use view default
            field = default_ordering.lstrip('-')

        # Validate sort_dir
        if sort_dir is not None:
            if sort_dir not in ('asc', 'desc'):
                raise ValidationError(
                    f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'."
                )
            direction = sort_dir
        else:
            # Infer direction from default_ordering prefix
            direction = 'desc' if default_ordering.startswith('-') else 'asc'

        # DRF ordering convention: prefix '-' for descending
        return [f'-{field}' if direction == 'desc' else field]

    def filter_queryset(self, request: Request, queryset, view: APIView):
        ordering = self.get_ordering(request, queryset, view)
        if ordering:
            return queryset.order_by(*ordering)
        return queryset


def build_raw_order_clause(
    sort_by: Optional[str],
    sort_dir: Optional[str],
    whitelist: list[str],
    default: str,
) -> str:
    """
    Build a safe SQL ORDER BY clause for raw queries.

    Args:
        sort_by:   value of 'sort_by' query param (may be None)
        sort_dir:  value of 'sort_dir' query param (may be None)
        whitelist: allowed column names
        default:   default column name (no prefix)

    Returns:
        String like '"name" ASC' ready to embed in ORDER BY.

    Raises:
        ValidationError if sort_by or sort_dir value is invalid.
    """
    if sort_by is not None:
        if sort_by not in whitelist:
            raise ValidationError(
                f"Invalid sort_by '{sort_by}'. Allowed: {whitelist}"
            )
        column = sort_by
    else:
        column = default

    if sort_dir is not None:
        if sort_dir not in ('asc', 'desc'):
            raise ValidationError(
                f"Invalid sort_dir '{sort_dir}'. Must be 'asc' or 'desc'."
            )
        direction = sort_dir.upper()
    else:
        direction = 'ASC'

    # Double-quote the column name to prevent SQL injection
    return f'"{column}" {direction}'
