"""
conftest.py для тестов directions.

Фаза 4: HMAC _make_cookie удалён. Аутентификация — JWT через корневые фикстуры.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope='session')
def django_db_setup():
    pass
