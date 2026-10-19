"""Тесты services.set_student_manager: валидация кандидата + синхронизация RenewalDeal.assignee."""
from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection

from apps.accounts.models import Account
from apps.renewals import engine
from apps.renewals.models import RenewalDeal
from apps.students import services


def _make_account(role: str, is_active: bool = True) -> int:
    """Создаёт аккаунт заданной роли. role='teacher' требует teacher_id
    (CHECK accounts_teacher_role_check) — заводим сопутствующую строку teachers."""
    email = f'__test_manager_svc__{uuid.uuid4().hex[:8]}@test.local'
    pw = make_password('testpass_sentinel')
    with connection.cursor() as cur:
        teacher_id = None
        if role == 'teacher':
            cur.execute(
                "INSERT INTO teachers (name) VALUES ('__test_manager_svc_teacher__') RETURNING id"
            )
            teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO accounts "
            "(email, password, role, teacher_id, is_active, is_staff, is_superuser, "
            "first_name, last_name, full_name, token_version, date_joined) "
            "VALUES (%s, %s, %s, %s, %s, false, false, '', '', %s, 0, NOW()) RETURNING id",
            [email, pw, role, teacher_id, is_active, f'__Test Manager {role}__'],
        )
        return cur.fetchone()[0]


def _make_student() -> int:
    name = f'__test_manager_svc_student__{uuid.uuid4().hex[:8]}'
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES (%s, now()) RETURNING id", [name])
        return cur.fetchone()[0]


def _cleanup(student_id: int, account_ids: list[int]) -> None:
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM renewal_activity WHERE deal_id IN '
            '(SELECT id FROM renewal_deal WHERE student_id = %s)', [student_id])
        cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [student_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])
        for acc_id in account_ids:
            cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])


@pytest.mark.django_db
def test_set_student_manager_updates_student():
    sid = _make_student()
    manager_id = _make_account('manager')
    try:
        result = services.set_student_manager(sid, manager_id)
        assert result is not None
        assert result['manager_id'] == manager_id
    finally:
        _cleanup(sid, [manager_id])


@pytest.mark.django_db
def test_set_student_manager_rejects_teacher_role():
    sid = _make_student()
    teacher_acc = _make_account('teacher')
    try:
        with pytest.raises(ValueError):
            services.set_student_manager(sid, teacher_acc)
    finally:
        _cleanup(sid, [teacher_acc])
        with connection.cursor() as cur:
            cur.execute("DELETE FROM teachers WHERE name = '__test_manager_svc_teacher__'")


@pytest.mark.django_db
def test_set_student_manager_rejects_inactive_account():
    sid = _make_student()
    manager_id = _make_account('manager', is_active=False)
    try:
        with pytest.raises(ValueError):
            services.set_student_manager(sid, manager_id)
    finally:
        _cleanup(sid, [manager_id])


@pytest.mark.django_db
def test_set_student_manager_returns_none_for_missing_student():
    manager_id = _make_account('manager')
    try:
        assert services.set_student_manager(999_999_999, manager_id) is None
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM accounts WHERE id = %s', [manager_id])


@pytest.mark.django_db
def test_set_student_manager_syncs_only_open_deal():
    """Синхронизация затрагивает только АКТИВНУЮ (открытую) сделку ученика —
    закрытые (won/lost) сохраняют своего исторического ответственного."""
    sid = _make_student()
    old_manager = _make_account('manager')
    new_manager = _make_account('admin')
    try:
        open_deal = engine.ensure_deal(sid, cycle_no=1)
        closed_deal = engine.ensure_deal(sid, cycle_no=2)
        RenewalDeal.objects.filter(id__in=[open_deal.id, closed_deal.id]).update(assignee_id=old_manager)
        closed_deal.outcome_at = closed_deal.stage_entered_at
        closed_deal.save(update_fields=['outcome_at'])

        services.set_student_manager(sid, new_manager)

        open_deal.refresh_from_db()
        closed_deal.refresh_from_db()
        assert open_deal.assignee_id == new_manager
        assert closed_deal.assignee_id == old_manager
    finally:
        _cleanup(sid, [old_manager, new_manager])


@pytest.mark.django_db
def test_set_student_manager_null_clears_assignee():
    sid = _make_student()
    manager_id = _make_account('manager')
    try:
        deal = engine.ensure_deal(sid, cycle_no=1)
        RenewalDeal.objects.filter(id=deal.id).update(assignee_id=manager_id)

        services.set_student_manager(sid, None)

        deal.refresh_from_db()
        assert deal.assignee_id is None
    finally:
        _cleanup(sid, [manager_id])
