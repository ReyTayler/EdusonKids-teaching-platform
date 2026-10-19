"""Шаги импорта не работают вне транзакции: падение на середине оставило бы
полуимпорт, который команда потом откажется доимпортировать (база не пуста).

Модуль намеренно без django_db: так шаг вызывается вне atomic-блока, и проверка
обязана сработать ДО первого запроса (иначе pytest-django запретил бы доступ к БД
другой ошибкой, и тест прошёл бы не по той причине — поэтому ловим именно
ImportDataError с текстом про транзакцию)."""
from __future__ import annotations

import datetime as dt

import pytest

from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_groups
from apps.eduson_import.steps.lessons import import_feedback, import_lessons
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.tests.factory import EdusonDB


@pytest.mark.parametrize('step', [import_people, import_groups, import_lessons, import_feedback])
def test_step_outside_transaction_is_refused(tmp_path, step):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.group(5, 'ПИ1001', 'python', 1)
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, dt.date(2026, 10, 18))
        with pytest.raises(ImportDataError, match='вне транзакции'):
            step(src, ctx)
