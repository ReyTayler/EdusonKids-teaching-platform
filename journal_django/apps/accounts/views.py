"""
Тонкие APIView для /api/admin/accounts (роль SUPERADMIN, управление доступами).

Зеркалит Express routes/admin/accounts.js (router.use(requireRole('admin'))):
  GET    /                      → список (без секретов) {rows,total,page,page_size}
  GET    /:id                   → одна учётка + teacher_name, без секретов | 404
  POST   /                      → 201 {id,email,role,teacher_id,invite_url,expires_at} | 409
  PATCH  /:id                   → 200 (без секретов) | 404
  POST   /:id/reset-password    → 200 {invite_url,expires_at} | 404
  POST   /:id/reset-2fa         → 200 {ok:true} | 404
  POST   /:id/invite/revoke     → 200 {ok:true} | 404
  POST   /:id/invite            → 200 {invite_url,expires_at} | 404
  POST   /:id/set-active        → 200 {ok:true,active} | 404
  DELETE /:id                   → 204 | 404 (физическое удаление; отключение — через set-active)

⚠️ Ни один ответ не содержит password_hash / twofa_secret.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services
from apps.accounts.serializers import AccountCreateSerializer, AccountUpdateSerializer
from apps.core.permissions import IsSuperAdmin

_DEFAULT_SORT_BY = 'email'
_DEFAULT_SORT_DIR = 'asc'
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500


def _parse_list_params(request: Request) -> dict:
    """parsePaginationRequest: page/page_size/sort_by/sort_dir/filter[*]. Тихий fallback сорта."""
    qp = request.query_params

    try:
        page = max(1, int(qp.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        raw_page_size = int(qp.get('page_size') or 0)
    except (TypeError, ValueError):
        raw_page_size = 0
    page_size = min(_MAX_PAGE_SIZE, max(1, raw_page_size or _DEFAULT_PAGE_SIZE))

    sort_by = qp.get('sort_by') or _DEFAULT_SORT_BY
    sort_dir = qp.get('sort_dir')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = _DEFAULT_SORT_DIR

    filters: dict = {}
    for key, value in qp.items():
        if key.startswith('filter[') and key.endswith(']'):
            filters[key[7:-1]] = value

    return {
        'page': page,
        'page_size': page_size,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'filters': filters,
    }


class AccountListCreateView(APIView):
    """GET список / POST создание учётки."""

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.list_accounts(**_parse_list_params(request)))

    def post(self, request: Request) -> Response:
        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = services.create_account(
            serializer.validated_data,
            actor_account_id=request.user.id,
            request=request,
        )
        if result.get('error') == 'email_taken':
            return Response(
                {'error': 'Email уже используется'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(result, status=status.HTTP_201_CREATED)


class AccountDetailView(APIView):
    """GET одна учётка / PATCH / DELETE (физическое удаление)."""

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request, pk: int) -> Response:
        acc = services.get_account(pk)
        if acc is None:
            raise NotFound({'error': 'Not found'})
        return Response(acc)

    def patch(self, request: Request, pk: int) -> Response:
        serializer = AccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated = services.update_account(pk, serializer.validated_data)
        if updated is None:
            raise NotFound({'error': 'Not found'})
        return Response(updated)

    def delete(self, request: Request, pk: int) -> Response:
        ok = services.hard_delete(pk, actor_account_id=request.user.id, request=request)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)


class AccountSetActiveView(APIView):
    """POST /:id/set-active — {active: bool}. Отключить/включить учётку."""

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        active = bool(request.data.get('active'))
        ok = services.set_active(pk, active, actor_account_id=request.user.id, request=request)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response({'ok': True, 'active': active})


class AccountResetPasswordView(APIView):
    """POST /:id/reset-password — новый temp-пароль."""

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        result = services.reset_password(pk, actor_account_id=request.user.id, request=request)
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


class AccountReset2faView(APIView):
    """POST /:id/reset-2fa — сброс 2FA + recovery-кодов."""

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        ok = services.reset_twofa(pk, actor_account_id=request.user.id, request=request)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response({'ok': True})


class AccountInviteRevokeView(APIView):
    """POST /:id/invite/revoke — отзыв активных инвайтов аккаунта."""

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        ok = services.revoke_invite(pk, actor_account_id=request.user.id, request=request)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response({'ok': True})


class AccountInviteView(APIView):
    """POST /:id/invite — перевыпустить invite-ссылку для установки пароля."""

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        result = services.regenerate_invite(
            pk, actor_account_id=request.user.id, request=request
        )
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)
