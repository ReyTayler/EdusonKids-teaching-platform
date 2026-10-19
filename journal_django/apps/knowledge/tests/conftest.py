"""
conftest.py тестов базы знаний.

django_db_setup — no-op, как в большинстве приложений проекта: тесты идут в
персистентную eduson_test и не пересоздают схему. Таблицы knowledge_* заводятся
в ней один раз командой:

    ./.venv/Scripts/python.exe manage.py migrate knowledge --settings=config.settings.test

Свежую test_eduson_test здесь не создаём намеренно: приложение добавляет только
новые таблицы и ничего не меняет в существующих, поэтому расходиться схемам
не на чем, а полный прогон 133 миграций на каждый запуск стоил бы минут.
"""
from __future__ import annotations

import pytest
from django.db import connection

# Префикс, по которому тесты узнают свои данные в общей eduson_test.
KB_TEST_PREFIX = '__test_kb_'


def cleanup_kb() -> None:
    """
    Убрать тестовые данные раздела.

    Дочерние таблицы удаляются первыми и поимённо. Каскад в проекте живёт в
    ORM, а не в БД (FK создаются DEFERRABLE INITIALLY DEFERRED без ON DELETE),
    тесты же чистят сырым SQL в обход ORM. Забытая дочерняя таблица не падает
    на месте: строка остаётся висеть и всплывает в конце теста на
    SET CONSTRAINTS ALL IMMEDIATE — в момент, когда связь с причиной уже не
    видна. Поэтому список ведём здесь один на все тесты раздела: новая
    дочерняя таблица дописывается в одно место, а не в четыре.
    """
    docs = (
        "SELECT id FROM knowledge_documents WHERE title LIKE '__test_kb_%'"
    )
    with connection.cursor() as cur:
        for table, column in (
            ('knowledge_image_usages', 'document_id'),
            ('knowledge_file_usages', 'document_id'),
            ('knowledge_favorites', 'document_id'),
        ):
            cur.execute(f'DELETE FROM {table} WHERE {column} IN ({docs})')  # noqa: S608
        cur.execute(
            "DELETE FROM knowledge_image_usages WHERE image_id IN "
            "(SELECT id FROM knowledge_images WHERE original_name LIKE '__test_kb_%')"
        )
        cur.execute("DELETE FROM knowledge_documents WHERE title LIKE '__test_kb_%'")
        cur.execute("DELETE FROM knowledge_sections WHERE title LIKE '__test_kb_%'")
        cur.execute(
            "DELETE FROM knowledge_file_usages WHERE file_id IN "
            "(SELECT id FROM knowledge_files WHERE original_name LIKE '__test_kb_%')"
        )
        cur.execute("DELETE FROM knowledge_images WHERE original_name LIKE '__test_kb_%'")
        cur.execute("DELETE FROM knowledge_files WHERE original_name LIKE '__test_kb_%'")


@pytest.fixture
def kb_clean():
    """Чистая база раздела до и после теста."""
    cleanup_kb()
    yield
    cleanup_kb()


@pytest.fixture(scope='session')
def django_db_setup():
    pass
