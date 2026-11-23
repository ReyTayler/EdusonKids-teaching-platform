"""API-тесты для /api/admin/teachers/:id/comments."""
from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection

from conftest import make_auth_client

BASE_URL = '/api/admin/teachers'


def _create_teacher(active: bool = True) -> int:
    name = f'__test_api_comment_teacher__{uuid.uuid4().hex[:8]}'
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO teachers (name, active) VALUES (%s, %s) RETURNING id',
            [name, active],
        )
        return cur.fetchone()[0]


def _cleanup_teacher(teacher_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teacher_comment WHERE teacher_id = %s', [teacher_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


def _insert_comment(teacher_id: int, body: str) -> int:
    from apps.teachers.models import TeacherComment
    return TeacherComment.objects.create(teacher_id=teacher_id, body=body).id


def _comment_exists(comment_id: int) -> bool:
    with connection.cursor() as cur:
        cur.execute('SELECT 1 FROM teacher_comment WHERE id = %s', [comment_id])
        return cur.fetchone() is not None


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


@pytest.fixture
def teacher_id(db):
    tid = _create_teacher()
    yield tid
    _cleanup_teacher(tid)


# ---------------------------------------------------------------------------
# Права
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_anon_gets_401_on_every_method(anon_client, teacher_id):
    comment_id = _insert_comment(teacher_id, 'x')
    assert anon_client.get(f'{BASE_URL}/{teacher_id}/comments').status_code == 401
    assert anon_client.post(
        f'{BASE_URL}/{teacher_id}/comments', {'body': 'x'}, format='json',
    ).status_code == 401
    assert anon_client.delete(
        f'{BASE_URL}/{teacher_id}/comments/{comment_id}',
    ).status_code == 401
    assert _comment_exists(comment_id)


@pytest.mark.django_db
def test_teacher_role_gets_403_on_every_method(teacher_client, teacher_id):
    """Карточка преподавателя закрыта для роли teacher — комментарии тоже."""
    comment_id = _insert_comment(teacher_id, 'x')
    assert teacher_client.get(f'{BASE_URL}/{teacher_id}/comments').status_code == 403
    assert teacher_client.post(
        f'{BASE_URL}/{teacher_id}/comments', {'body': 'x'}, format='json',
    ).status_code == 403
    assert teacher_client.delete(
        f'{BASE_URL}/{teacher_id}/comments/{comment_id}',
    ).status_code == 403
    assert _comment_exists(comment_id)


@pytest.mark.django_db
def test_manager_can_create_and_list(manager_client, teacher_id):
    resp = manager_client.post(
        f'{BASE_URL}/{teacher_id}/comments',
        {'body': 'Просил не ставить уроки по пятницам'},
        format='json',
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {'id', 'body', 'created_at', 'author_id', 'author_name'}
    assert body['body'] == 'Просил не ставить уроки по пятницам'
    assert body['author_id'] is not None

    resp = manager_client.get(f'{BASE_URL}/{teacher_id}/comments')
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) >= {'rows', 'total', 'page', 'page_size'}
    assert data['total'] == 1
    assert data['rows'][0]['id'] == body['id']
    assert data['rows'][0]['body'] == 'Просил не ставить уроки по пятницам'


@pytest.mark.django_db
def test_manager_cannot_delete(manager_client, teacher_id):
    comment_id = _insert_comment(teacher_id, 'x')
    resp = manager_client.delete(f'{BASE_URL}/{teacher_id}/comments/{comment_id}')
    assert resp.status_code == 403
    assert _comment_exists(comment_id)


@pytest.mark.django_db
def test_admin_can_delete(admin_client, teacher_id):
    comment_id = _insert_comment(teacher_id, 'x')
    resp = admin_client.delete(f'{BASE_URL}/{teacher_id}/comments/{comment_id}')
    assert resp.status_code == 204
    assert not _comment_exists(comment_id)
    resp = admin_client.delete(f'{BASE_URL}/{teacher_id}/comments/{comment_id}')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_superadmin_full_access(superadmin_client, teacher_id):
    resp = superadmin_client.post(
        f'{BASE_URL}/{teacher_id}/comments', {'body': 'x'}, format='json',
    )
    assert resp.status_code == 201
    comment_id = resp.json()['id']
    assert superadmin_client.get(f'{BASE_URL}/{teacher_id}/comments').json()['total'] == 1
    resp = superadmin_client.delete(f'{BASE_URL}/{teacher_id}/comments/{comment_id}')
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# 404
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_unknown_teacher_list_returns_404(admin_client):
    assert admin_client.get(f'{BASE_URL}/999999999/comments').status_code == 404


@pytest.mark.django_db
def test_unknown_teacher_create_returns_404(admin_client):
    resp = admin_client.post(f'{BASE_URL}/999999999/comments', {'body': 'x'}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_delete_unknown_comment_returns_404(admin_client, teacher_id):
    resp = admin_client.delete(f'{BASE_URL}/{teacher_id}/comments/999999999')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_delete_other_teachers_comment_returns_404(admin_client, teacher_id):
    """id чужого комментария под URL другого преподавателя — 404, строка цела."""
    other_id = _create_teacher()
    try:
        foreign_comment = _insert_comment(other_id, 'чужой')
        resp = admin_client.delete(f'{BASE_URL}/{teacher_id}/comments/{foreign_comment}')
        assert resp.status_code == 404
        assert _comment_exists(foreign_comment)
    finally:
        _cleanup_teacher(other_id)


@pytest.mark.django_db
def test_inactive_teacher_comments_available(admin_client):
    """Карточка отдаёт и неактивного (soft-delete) преподавателя — комментарии тоже."""
    tid = _create_teacher(active=False)
    try:
        resp = admin_client.post(f'{BASE_URL}/{tid}/comments', {'body': 'x'}, format='json')
        assert resp.status_code == 201
    finally:
        _cleanup_teacher(tid)


# ---------------------------------------------------------------------------
# Валидация body
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_blank_body_returns_400(admin_client, teacher_id):
    resp = admin_client.post(f'{BASE_URL}/{teacher_id}/comments', {'body': '  '}, format='json')
    assert resp.status_code == 400
    assert admin_client.get(f'{BASE_URL}/{teacher_id}/comments').json()['total'] == 0


@pytest.mark.django_db
def test_create_missing_body_returns_400(admin_client, teacher_id):
    resp = admin_client.post(f'{BASE_URL}/{teacher_id}/comments', {}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_too_long_body_returns_400(admin_client, teacher_id):
    resp = admin_client.post(
        f'{BASE_URL}/{teacher_id}/comments', {'body': 'x' * 5001}, format='json',
    )
    assert resp.status_code == 400
    assert admin_client.get(f'{BASE_URL}/{teacher_id}/comments').json()['total'] == 0


@pytest.mark.django_db
def test_create_strips_body(admin_client, teacher_id):
    resp = admin_client.post(
        f'{BASE_URL}/{teacher_id}/comments', {'body': '  текст  '}, format='json',
    )
    assert resp.status_code == 201
    assert resp.json()['body'] == 'текст'


# ---------------------------------------------------------------------------
# Автор, порядок, пагинация
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_author_name_reflects_creator(teacher_id):
    client, acc_id = _create_named_admin_client('Иван Иванов')
    try:
        resp = client.post(f'{BASE_URL}/{teacher_id}/comments', {'body': 'Комментарий'}, format='json')
        assert resp.status_code == 201
        assert resp.json()['author_id'] == acc_id
        assert resp.json()['author_name'] == 'Иван Иванов'
        row = client.get(f'{BASE_URL}/{teacher_id}/comments').json()['rows'][0]
        assert row['author_id'] == acc_id
        assert row['author_name'] == 'Иван Иванов'
    finally:
        _cleanup_teacher(teacher_id)
        _cleanup_account(acc_id)


@pytest.mark.django_db
def test_list_scoped_to_teacher_and_newest_first(admin_client, teacher_id):
    other_id = _create_teacher()
    try:
        _insert_comment(other_id, 'чужой')
        first = _insert_comment(teacher_id, 'первый')
        second = _insert_comment(teacher_id, 'второй')
        third = _insert_comment(teacher_id, 'третий')
        data = admin_client.get(f'{BASE_URL}/{teacher_id}/comments').json()
        assert data['total'] == 3
        assert [r['id'] for r in data['rows']] == [third, second, first]
    finally:
        _cleanup_teacher(other_id)


@pytest.mark.django_db
def test_pagination(admin_client, teacher_id):
    ids = [_insert_comment(teacher_id, f'c{i}') for i in range(5)]
    page1 = admin_client.get(f'{BASE_URL}/{teacher_id}/comments?page=1&page_size=2').json()
    page3 = admin_client.get(f'{BASE_URL}/{teacher_id}/comments?page=3&page_size=2').json()
    assert page1['total'] == 5
    assert page1['page'] == 1
    assert page1['page_size'] == 2
    assert [r['id'] for r in page1['rows']] == [ids[4], ids[3]]
    assert [r['id'] for r in page3['rows']] == [ids[0]]


@pytest.mark.django_db
def test_same_created_at_order_is_deterministic(admin_client, teacher_id):
    """Одинаковый created_at (импорт, одна транзакция) — тай-брейк по id, страницы не едут."""
    ids = [_insert_comment(teacher_id, f'c{i}') for i in range(3)]
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE teacher_comment SET created_at = '2026-01-01T10:00:00+03:00' "
            'WHERE teacher_id = %s', [teacher_id],
        )
    rows = admin_client.get(f'{BASE_URL}/{teacher_id}/comments').json()['rows']
    assert [r['id'] for r in rows] == sorted(ids, reverse=True)


@pytest.mark.django_db
def test_list_has_no_n_plus_one(admin_client, teacher_id, django_assert_max_num_queries):
    """Автор подтягивается select_related — число запросов не растёт с числом строк."""
    # Через API, чтобы у каждой строки был автор: без автора author_name не
    # обращается к связи, и N+1 тест бы не поймал.
    for i in range(10):
        resp = admin_client.post(f'{BASE_URL}/{teacher_id}/comments', {'body': f'c{i}'}, format='json')
        assert resp.status_code == 201
    # аутентификация + exists-гейт + count + страница (с запасом на служебные).
    with django_assert_max_num_queries(6):
        resp = admin_client.get(f'{BASE_URL}/{teacher_id}/comments')
    assert resp.status_code == 200
