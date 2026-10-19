"""
conftest.py для тестов students.

Фаза 4: HMAC _make_cookie удалён. Аутентификация — JWT через корневые фикстуры.
managed=False — работаем с eduson_test, чистим прямым DELETE.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope='session')
def django_db_setup():
    pass
