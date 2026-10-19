"""API-тесты /api/admin/students/:id/manager — доступ только admin/superadmin."""
from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection

BASE_URL = '/api/admin/students'


def _create_student() -> int:
    name = f'__test_manager_api_student__{uuid.uuid4().hex[:8]}'
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES (%s, NOW()) RETURNING id", [name])
        return cur.fetchone()[0]


def _create_account(role: str, is_active: bool = True) -> int:
    """role='teacher' требует teacher_id (CHECK accounts_teacher_role_check) —
    заводим сопутствующую строку teachers (см. test_manager_service.py::_make_account).
    Имя uuid-суффиксировано, как и остальные тестовые сущности в файле."""
    email = f'__test_manager_api_acc__{uuid.uuid4().hex[:8]}@test.local'
    pw = make_password('testpass_sentinel')
    with connection.cursor() as cur:
        teacher_id = None
        if role == 'teacher':
            teacher_name = f'__test_manager_api_teacher__{uuid.uuid4().hex[:8]}'
            cur.execute(
                'INSERT INTO teachers (name) VALUES (%s) RETURNING id', [teacher_name])
            teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO accounts "
            "(email, password, role, teacher_id, is_active, is_staff, is_superuser, "
            "first_name, last_name, token_version, date_joined) "
            "VALUES (%s, %s, %s, %s, %s, false, false, '', '', 0, NOW()) RETURNING id",
            [email, pw, role, teacher_id, is_active],
        )
        return cur.fetchone()[0]


def _cleanup_student(student_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


def _cleanup_account(acc_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('SELECT teacher_id FROM accounts WHERE id = %s', [acc_id])
        row = cur.fetchone()
        teacher_id = row[0] if row else None
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])
        if teacher_id is not None:
            cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.mark.django_db
def test_manager_update_forbidden_for_manager_role(manager_client):
    sid = _create_student()
    acc_id = _create_account('manager')
    try:
        resp = manager_client.patch(f'{BASE_URL}/{sid}/manager', {'manager_id': acc_id}, format='json')
        assert resp.status_code == 403
    finally:
        _cleanup_student(sid)
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_manager_update_allowed_for_admin(admin_client):
    sid = _create_student()
    acc_id = _create_account('manager')
    try:
        resp = admin_client.patch(f'{BASE_URL}/{sid}/manager', {'manager_id': acc_id}, format='json')
        assert resp.status_code == 200
        assert resp.json()['manager_id'] == acc_id
    finally:
        _cleanup_student(sid)
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_manager_update_allowed_for_superadmin(superadmin_client):
    sid = _create_student()
    acc_id = _create_account('admin')
    try:
        resp = superadmin_client.patch(f'{BASE_URL}/{sid}/manager', {'manager_id': acc_id}, format='json')
        assert resp.status_code == 200
        assert resp.json()['manager_id'] == acc_id
    finally:
        _cleanup_student(sid)
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_manager_update_rejects_teacher_account(admin_client):
    sid = _create_student()
    acc_id = _create_account('teacher')
    try:
        resp = admin_client.patch(f'{BASE_URL}/{sid}/manager', {'manager_id': acc_id}, format='json')
        assert resp.status_code == 400
    finally:
        _cleanup_student(sid)
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_manager_update_null_clears_manager(admin_client):
    sid = _create_student()
    acc_id = _create_account('manager')
    try:
        admin_client.patch(f'{BASE_URL}/{sid}/manager', {'manager_id': acc_id}, format='json')
        resp = admin_client.patch(f'{BASE_URL}/{sid}/manager', {'manager_id': None}, format='json')
        assert resp.status_code == 200
        assert resp.json()['manager_id'] is None
    finally:
        _cleanup_student(sid)
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_manager_update_404_for_missing_student(admin_client):
    acc_id = _create_account('manager')
    try:
        resp = admin_client.patch(f'{BASE_URL}/999999999/manager', {'manager_id': acc_id}, format='json')
        assert resp.status_code == 404
    finally:
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_manager_field_not_writable_via_general_patch(admin_client):
    """Общий PATCH /students/:id молча игнорирует manager_id — поле не объявлено
    в StudentUpdateSerializer, даже когда его шлёт admin (не только manager)."""
    sid = _create_student()
    acc_id = _create_account('manager')
    try:
        resp = admin_client.patch(f'{BASE_URL}/{sid}', {'manager_id': acc_id, 'birth_date': '2015-03-10'}, format='json')
        assert resp.status_code == 200
        assert resp.json()['manager_id'] is None
        # birth_date — обычное записываемое поле, применилось; manager_id — нет.
        assert resp.json()['birth_date'] == '2015-03-10'
    finally:
        _cleanup_student(sid)
        _cleanup_account(acc_id)
