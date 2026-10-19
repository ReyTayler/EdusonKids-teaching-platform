"""
Test settings — изолированная test-БД форка Eduson.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ: модели проекта managed=False, а conftest части приложений
переопределяет django_db_setup на pass — поэтому pytest ходит в ту БД, что указана
в настройках. Полный прогон делает flush целевой БД, и если это рабочая БД —
реальные данные стираются.

ЗАЩИТА: тесты всегда идут в отдельную БД `eduson_test`, а ниже стоит fail-fast
guard по белому списку имён форка: он НЕ ДАСТ запустить тесты ни против рабочей
`eduson`, ни против любых БД KOTOKOD на той же машине (`journal`, `journal_test`,
`test_journal_test`), даже если кто-то переопределит DATABASE_URL/TEST_DB_NAME.
pytest.ini указывает на этот модуль настроек.

Схема test-БД строится миграциями: scripts/recreate_test_db.sh (в KOTOKOD она
клонировалась из схемы прода — у форка своей рабочей схемы-образца нет).
"""
import re

from django.core.exceptions import ImproperlyConfigured

from .development import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Test-БД: тот же сервер/креды, что и рабочая, но отдельное имя.
# Имя можно переопределить через TEST_DB_NAME (по умолчанию eduson_test).
# ---------------------------------------------------------------------------
_TEST_DB_NAME = env('TEST_DB_NAME', default='eduson_test')  # noqa: F405

DATABASES['default'] = {  # noqa: F405
    **DATABASES['default'],  # noqa: F405  — host/port/user/password из DATABASE_URL
    'NAME': _TEST_DB_NAME,
}

# ---------------------------------------------------------------------------
# FAIL-FAST GUARD — никогда не запускать тесты против чужой или рабочей БД.
# managed=False + django_db_setup=pass означают, что pytest работает с РЕАЛЬНЫМИ
# таблицами (без создания test_*-БД), а фабрики делают DELETE/flush.
#
# Белый список, а не «оканчивается на _test»: на той же машине живут тестовые БД
# KOTOKOD (journal_test, test_journal_test) — они тоже *_test, и flush из форка
# стёр бы их. Разрешены только имена форка. Тот же шаблон — в
# scripts/recreate_test_db.sh. Проверяется настроенное NAME (eduson_test);
# производную test_eduson_test pytest-django выводит из него сам, мимо guard'а.
# ---------------------------------------------------------------------------
_SAFE_TEST_DB_RE = re.compile(r'^eduson[a-z0-9_]*_test$')


def _assert_safe_test_db(name: str) -> None:
    if not _SAFE_TEST_DB_RE.fullmatch(name or ''):
        raise ImproperlyConfigured(
            f"Отказ запуска тестов: БД '{name}' не тестовая БД форка Eduson. "
            f"Разрешены только имена вида eduson*_test (по умолчанию eduson_test). "
            f"Создать её из миграций: scripts/recreate_test_db.sh. "
            f"Полный прогон pytest делает flush указанной БД."
        )


_assert_safe_test_db(DATABASES['default']['NAME'])  # noqa: F405

# Письма в тестах копятся в mail.outbox, реальная отправка не нужна.
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
