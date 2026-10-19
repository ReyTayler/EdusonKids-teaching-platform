"""API-тесты для /api/admin/students/:id/comments."""
from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection

from conftest import make_auth_client

BASE_URL = '/api/admin/students'


def _create_student() -> int:
    name = f'__test_api_comment_student__{uuid.uuid4().hex[:8]}'
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name, created_at) "
            "VALUES (%s, NOW()) RETURNING id",
            [name],
        )
        return cur.fetchone()[0]


def _cleanup_student(student_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM student_comment WHERE student_id = %s', [student_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


def _create_named_admin_client(full_name: str):
    from apps.accounts.models import Account
    email = f'__test_named_admin__{uuid.uuid4().hex[:8]}@test.local'
    pw = make_password('testpass_sentinel')
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts "
            "(email, password, role, is_active, is_staff, is_superuser, "
            "first_name, last_name, full_name, token_version, date_joined) "
            "VALUES (%s, %s, 'admin', true, false, false, '', '', %s, 0, NOW()) RETURNING id",
            [email, pw, full_name],
        )
        acc_id = cur.fetchone()[0]
    account = Account.objects.get(pk=acc_id)
    return make_auth_client(account), acc_id


def _cleanup_account(acc_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])


@pytest.mark.django_db
def test_no_cookie_returns_401(anon_client):
    student_id = _create_student()
    try:
        resp = anon_client.get(f'{BASE_URL}/{student_id}/comments')
        assert resp.status_code == 401
    finally:
        _cleanup_student(student_id)


@pytest.mark.django_db
def test_teacher_cannot_list(teacher_client):
    student_id = _create_student()
    try:
        resp = teacher_client.get(f'{BASE_URL}/{student_id}/comments')
        assert resp.status_code == 403
    finally:
        _cleanup_student(student_id)


@pytest.mark.django_db
def test_unknown_student_returns_404(admin_client):
    resp = admin_client.get(f'{BASE_URL}/999999999/comments')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_manager_can_create_and_list(manager_client):
    student_id = _create_student()
    try:
        resp = manager_client.post(
            f'{BASE_URL}/{student_id}/comments',
            {'body': 'Родители просили перенести занятия'},
            format='json',
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body['body'] == 'Родители просили перенести занятия'
        assert 'author_name' in body

        resp = manager_client.get(f'{BASE_URL}/{student_id}/comments')
        assert resp.status_code == 200
        data = resp.json()
        assert data['total'] == 1
        assert data['rows'][0]['body'] == 'Родители просили перенести занятия'
    finally:
        _cleanup_student(student_id)


@pytest.mark.django_db
def test_create_blank_body_returns_400(admin_client):
    student_id = _create_student()
    try:
        resp = admin_client.post(f'{BASE_URL}/{student_id}/comments', {'body': '  '}, format='json')
        assert resp.status_code == 400
    finally:
        _cleanup_student(student_id)


@pytest.mark.django_db
def test_author_name_reflects_creator():
    """author_name в ответе — реальное имя автора (join на accounts)."""
    student_id = _create_student()
    client, acc_id = _create_named_admin_client('Иван Иванов')
    try:
        resp = client.post(f'{BASE_URL}/{student_id}/comments', {'body': 'Комментарий'}, format='json')
        assert resp.status_code == 201
        resp = client.get(f'{BASE_URL}/{student_id}/comments')
        row = resp.json()['rows'][0]
        assert row['author_name'] == 'Иван Иванов'
    finally:
        _cleanup_student(student_id)
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_manager_cannot_delete(manager_client):
    student_id = _create_student()
    try:
        resp = manager_client.post(f'{BASE_URL}/{student_id}/comments', {'body': 'x'}, format='json')
        comment_id = resp.json()['id']
        resp = manager_client.delete(f'{BASE_URL}/{student_id}/comments/{comment_id}')
        assert resp.status_code == 403
    finally:
        _cleanup_student(student_id)


@pytest.mark.django_db
def test_admin_can_delete(admin_client):
    student_id = _create_student()
    try:
        resp = admin_client.post(f'{BASE_URL}/{student_id}/comments', {'body': 'x'}, format='json')
        comment_id = resp.json()['id']
        resp = admin_client.delete(f'{BASE_URL}/{student_id}/comments/{comment_id}')
        assert resp.status_code == 204
        resp = admin_client.delete(f'{BASE_URL}/{student_id}/comments/{comment_id}')
        assert resp.status_code == 404
    finally:
        _cleanup_student(student_id)
