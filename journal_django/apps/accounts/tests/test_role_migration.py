"""Проверяет, что после миграций в БД разрешена роль superadmin (CHECK-констрейнт)."""
from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def test_superadmin_role_accepted_by_db():
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (email, password, role, is_active, is_staff, is_superuser, "
            "first_name, last_name, full_name, date_joined, token_version) "
            "VALUES ('__mig_super__@example.com', '!', 'superadmin', true, false, false, '', '', NULL, NOW(), 0) "
            "RETURNING id",
        )
        acc_id = cur.fetchone()[0]
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])
