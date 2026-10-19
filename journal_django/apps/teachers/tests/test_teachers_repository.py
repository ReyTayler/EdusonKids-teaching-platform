"""
Unit-тесты для TeachersRepository.

Работают с реальной БД (managed=False, продовая).
Все созданные строки удаляются в teardown.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.teachers import repository


def _cleanup_teacher(teacher_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


@pytest.mark.django_db
def test_list_teachers_returns_list():
    result = repository.list_teachers()
    assert isinstance(result, list)


@pytest.mark.django_db
def test_list_teachers_active_only():
    result = repository.list_teachers(include_inactive=False)
    for row in result:
        assert row['active'] is True


@pytest.mark.django_db
def test_list_teachers_include_inactive():
    result = repository.list_teachers(include_inactive=True)
    assert isinstance(result, list)
    # Могут быть неактивные — просто убеждаемся что не упало


@pytest.mark.django_db
def test_get_teacher_nonexistent_returns_none():
    result = repository.get_teacher(999999999)
    assert result is None


@pytest.mark.django_db
def test_create_and_get_teacher():
    teacher = repository.create_teacher({'name': '__test_repo_teacher__'})
    assert teacher is not None
    teacher_id = teacher['id']
    try:
        fetched = repository.get_teacher(teacher_id)
        assert fetched is not None
        assert fetched['name'] == '__test_repo_teacher__'
        assert fetched['active'] is True
    finally:
        _cleanup_teacher(teacher_id)


@pytest.mark.django_db
def test_create_teacher_with_email_phone():
    teacher = repository.create_teacher({
        'name': '__test_repo_teacher_ep__',
        'email': 'test@example.com',
        'phone': '+7-999-123-4567',
    })
    assert teacher is not None
    teacher_id = teacher['id']
    try:
        assert teacher['email'] == 'test@example.com'
        assert teacher['phone'] == '+7-999-123-4567'
    finally:
        _cleanup_teacher(teacher_id)


@pytest.mark.django_db
def test_update_teacher_name():
    teacher = repository.create_teacher({'name': '__test_repo_update__'})
    teacher_id = teacher['id']
    try:
        updated = repository.update_teacher(teacher_id, {'name': '__test_repo_updated__'})
        assert updated is not None
        assert updated['name'] == '__test_repo_updated__'
    finally:
        _cleanup_teacher(teacher_id)


@pytest.mark.django_db
def test_update_teacher_active_false():
    teacher = repository.create_teacher({'name': '__test_repo_deactivate__'})
    teacher_id = teacher['id']
    try:
        updated = repository.update_teacher(teacher_id, {'active': False})
        assert updated is not None
        assert updated['active'] is False
    finally:
        _cleanup_teacher(teacher_id)


@pytest.mark.django_db
def test_update_teacher_nonexistent_returns_none():
    result = repository.update_teacher(999999999, {'name': 'ghost'})
    assert result is None


@pytest.mark.django_db
def test_soft_delete_teacher():
    teacher = repository.create_teacher({'name': '__test_repo_softdel__'})
    teacher_id = teacher['id']
    try:
        ok = repository.soft_delete_teacher(teacher_id)
        assert ok is True
        fetched = repository.get_teacher(teacher_id)
        assert fetched['active'] is False
    finally:
        _cleanup_teacher(teacher_id)


@pytest.mark.django_db
def test_soft_delete_teacher_nonexistent():
    ok = repository.soft_delete_teacher(999999999)
    assert ok is False
