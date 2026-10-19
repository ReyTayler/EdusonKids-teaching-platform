"""
Permission classes for journal_django.
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.request import Request
from rest_framework.views import APIView


def _authenticated_with_role(request: Request, *roles: str) -> bool:
    """Return True if the request user is authenticated and has one of the given roles."""
    user = request.user
    # UNAUTHENTICATED_USER=None → при отсутствии токена request.user is None.
    # Зеркалим штатный DRF IsAuthenticated: сперва truthy-проверка, потом is_authenticated.
    if not (user and user.is_authenticated):
        return False
    return user.role in roles


class IsTeacher(BasePermission):
    """Allow access only to accounts with role 'teacher'."""
    message = 'Teacher role required.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _authenticated_with_role(request, 'teacher')


class IsManager(BasePermission):
    """Allow access only to accounts with role 'manager'."""
    message = 'Manager role required.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _authenticated_with_role(request, 'manager')


class IsAdmin(BasePermission):
    """Allow access only to accounts with role 'admin'."""
    message = 'Admin role required.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _authenticated_with_role(request, 'admin')


class IsManagerOrAdmin(BasePermission):
    """Allow access to manager, admin or superadmin."""
    message = 'Manager or admin role required.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _authenticated_with_role(request, 'manager', 'admin', 'superadmin')


class IsSuperAdmin(BasePermission):
    """Allow access only to superadmin."""
    message = 'Superadmin role required.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _authenticated_with_role(request, 'superadmin')


class IsAdminOrSuperAdmin(BasePermission):
    """Allow access to admin or superadmin."""
    message = 'Admin or superadmin role required.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _authenticated_with_role(request, 'admin', 'superadmin')


class ReadStaffWriteSuperAdmin(BasePermission):
    """SAFE-методы — manager/admin/superadmin; мутации — только superadmin."""
    message = 'Read for staff; write for superadmin only.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        if request.method in SAFE_METHODS:
            return _authenticated_with_role(request, 'manager', 'admin', 'superadmin')
        return _authenticated_with_role(request, 'superadmin')


class ReadStaffWriteAdmin(BasePermission):
    """SAFE-методы — manager/admin/superadmin; мутации — admin/superadmin."""
    message = 'Read for staff; write for admin or superadmin.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        if request.method in SAFE_METHODS:
            return _authenticated_with_role(request, 'manager', 'admin', 'superadmin')
        return _authenticated_with_role(request, 'admin', 'superadmin')