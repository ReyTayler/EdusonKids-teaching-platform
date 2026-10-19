"""Форк Eduson не должен делить ресурсы с KOTOKOD на одной машине."""
import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings.test import _assert_safe_test_db


def test_test_db_is_eduson():
    assert settings.DATABASES['default']['NAME'] in ('eduson_test', 'test_eduson_test')


@pytest.mark.parametrize('name', [
    'eduson',                           # рабочая БД форка
    'journal',                          # рабочая БД KOTOKOD
    'journal_test',                     # тестовая БД KOTOKOD — тоже *_test
    'test_journal_test',                # производная тестовая БД KOTOKOD
    'x; drop database journal; --_test',  # инъекция в идентификатор
    'eduson_test; drop database journal',
    'Eduson_test',
    '',
])
def test_guard_rejects_foreign_and_unsafe_names(name):
    with pytest.raises(ImproperlyConfigured):
        _assert_safe_test_db(name)


@pytest.mark.parametrize('name', ['eduson_test', 'eduson_e2e_test'])
def test_guard_accepts_eduson_test_names(name):
    _assert_safe_test_db(name)


def test_cache_namespace_is_eduson():
    cache = settings.CACHES['default']
    if settings.REDIS_URL:
        assert cache['KEY_PREFIX'] == 'eduson'
    else:
        assert cache['LOCATION'] == 'eduson-locmem'


def test_celery_broker_uses_own_redis_db():
    if settings.REDIS_URL:
        pytest.skip('REDIS_URL задан явно — брокер берётся из него')
    # /0 занят KOTOKOD на той же машине.
    assert settings.CELERY_BROKER_URL.endswith('/2')


def test_dev_origins_use_eduson_ports():
    from config.settings import development
    assert 'http://localhost:5183' in development.CORS_ALLOWED_ORIGINS
    assert all(':5173' not in o for o in development.CORS_ALLOWED_ORIGINS)
