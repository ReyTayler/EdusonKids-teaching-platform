"""
DiscountsView — тонкий ViewSet для /api/admin/discounts.

Зеркалит Express routes/admin/discounts.js:
  GET    /api/admin/discounts        → list()     → 200 [...]
  GET    /api/admin/discounts/:id    → retrieve() → 200 | 404
  POST   /api/admin/discounts        → create()   → 201
  PATCH  /api/admin/discounts/:id    → update()   → 200 | 404
  DELETE /api/admin/discounts/:id    → destroy()  → 204 | 404

Параметры: ?include_inactive=1 (GET список).
Права: чтение — manager/admin/superadmin; запись — только superadmin (ReadStaffWriteSuperAdmin).

Примечание: Express не ловит 409 при создании скидок (нет UNIQUE по name).
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ReadStaffWriteSuperAdmin
from apps.discounts import services
from apps.discounts.serializers import DiscountUpdateSerializer, DiscountWriteSerializer


class DiscountListCreateView(APIView):
    """
    GET  /api/admin/discounts  — список скидок
    POST /api/admin/discounts  — создать скидку
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request) -> Response:
        include_inactive = request.query_params.get('include_inactive') == '1'
        rows = services.list_discounts(include_inactive=include_inactive)
        return Response(rows)

    def post(self, request: Request) -> Response:
        serializer = DiscountWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        discount = services.create_discount(serializer.validated_data)
        return Response(discount, status=status.HTTP_201_CREATED)


class DiscountDetailView(APIView):
    """
    GET    /api/admin/discounts/:id  — получить скидку
    PATCH  /api/admin/discounts/:id  — обновить скидку
    DELETE /api/admin/discounts/:id  — мягкое удаление
    """

    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request, pk: int) -> Response:
        discount = services.get_discount(pk)
        if discount is None:
            raise NotFound({'error': 'Not found'})
        return Response(discount)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = DiscountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated = services.update_discount(pk, serializer.validated_data)
        if updated is None:
            raise NotFound({'error': 'Not found'})

        return Response(updated)

    def delete(self, request: Request, pk: int) -> Response:
        ok = services.soft_delete_discount(pk)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)
