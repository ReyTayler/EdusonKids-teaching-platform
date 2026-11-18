"""Фикстуры availability. Работаем в общей eduson_test (как большинство apps) —
no-op django_db_setup; каждый тест в транзакции pytest-django (откат)."""
from __future__ import annotations

import pytest
from django.utils import timezone

from apps.teachers.models import Teacher
from conftest import _create_account, _delete_account, _jwt_client_for_id


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def teacher(db):
    return Teacher.objects.create(name='__avail_teacher__', active=True, created_at=timezone.now())


@pytest.fixture
def other_teacher(db):
    return Teacher.objects.create(name='__avail_other__', active=True, created_at=timezone.now())


@pytest.fixture
def own_teacher_client(teacher):
    """JWT-клиент учётки role=teacher, привязанной к фикстуре teacher."""
    acc_id, _ = _create_account('__avail_t__@test.local', 'teacher', teacher_id=teacher.id)
    yield _jwt_client_for_id(acc_id)
    _delete_account(acc_id)
