"""Юнит-тесты permission-классов (без HTTP: мок request.user + method)."""
from __future__ import annotations

from types import SimpleNamespace

from rest_framework.permissions import SAFE_METHODS

from apps.core.permissions import (
    IsSuperAdmin,
    IsAdminOrSuperAdmin,
    IsManagerOrAdmin,
    ReadStaffWriteSuperAdmin,
    ReadStaffWriteAdmin,
)


def _req(role, method='GET'):
    user = SimpleNamespace(is_authenticated=True, role=role)
    return SimpleNamespace(user=user, method=method)


def test_is_superadmin():
    assert IsSuperAdmin().has_permission(_req('superadmin'), None) is True
    for r in ('admin', 'manager', 'teacher'):
        assert IsSuperAdmin().has_permission(_req(r), None) is False


def test_is_admin_or_superadmin():
    for r in ('admin', 'superadmin'):
        assert IsAdminOrSuperAdmin().has_permission(_req(r), None) is True
    for r in ('manager', 'teacher'):
        assert IsAdminOrSuperAdmin().has_permission(_req(r), None) is False


def test_manager_or_admin_includes_superadmin():
    for r in ('manager', 'admin', 'superadmin'):
        assert IsManagerOrAdmin().has_permission(_req(r), None) is True
    assert IsManagerOrAdmin().has_permission(_req('teacher'), None) is False


def test_read_staff_write_superadmin():
    p = ReadStaffWriteSuperAdmin()
    for r in ('manager', 'admin', 'superadmin'):
        assert p.has_permission(_req(r, 'GET'), None) is True
    for r in ('manager', 'admin'):
        assert p.has_permission(_req(r, 'POST'), None) is False
    assert p.has_permission(_req('superadmin', 'DELETE'), None) is True


def test_read_staff_write_admin():
    p = ReadStaffWriteAdmin()
    for r in ('manager', 'admin', 'superadmin'):
        assert p.has_permission(_req(r, 'GET'), None) is True
    assert p.has_permission(_req('manager', 'PATCH'), None) is False
    for r in ('admin', 'superadmin'):
        assert p.has_permission(_req(r, 'PATCH'), None) is True
