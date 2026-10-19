"""
Views раздела «Журнал изменений» (/api/admin/changelog).

RBAC: просмотр (лента, детали) и откат — только admin/superadmin
(журнал изменений закрыт для manager).
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.changelog import services
from apps.changelog.revert import RevertConflict, RevertError, RevertForbidden
from apps.core.permissions import IsAdminOrSuperAdmin


def _parse_list_params(request: Request) -> dict:
    qp = request.query_params
    page = max(1, int(qp.get('page', 1) or 1))
    page_size = min(200, max(1, int(qp.get('page_size', 50) or 50)))

    filters: dict = {}
    for key, value in qp.items():
        if key.startswith('filter[') and key.endswith(']'):
            filters[key[7:-1]] = value

    return {'page': page, 'page_size': page_size, 'filters': filters}


class ChangelogListView(APIView):
    """GET /api/admin/changelog — лента операций."""

    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.list_operations(**_parse_list_params(request)))


class ChangelogDetailView(APIView):
    """GET /api/admin/changelog/<uuid:context_id> — детали операции."""

    permission_classes = [IsAdminOrSuperAdmin]

    def get(self, request: Request, context_id) -> Response:
        data = services.get_operation(context_id)
        if data is None:
            raise NotFound('Операция не найдена.')
        return Response(data)


class ChangelogRevertView(APIView):
    """POST /api/admin/changelog/<uuid:context_id>/revert — откат операции."""

    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request: Request, context_id) -> Response:
        try:
            summary = services.revert_operation(context_id, request=request)
        except RevertConflict as exc:
            # Контракт фронта (lib/api.ts): message — из 'error',
            # структура — из 'details' (ApiError.details).
            return Response(
                {
                    'error': 'Данные изменились после этой операции — откат отклонён.',
                    'details': {'conflicts': exc.conflicts},
                },
                status=409,
            )
        except RevertForbidden as exc:
            return Response({'error': str(exc)}, status=400)
        except RevertError:
            raise NotFound('Операция не найдена.')
        return Response(summary)
