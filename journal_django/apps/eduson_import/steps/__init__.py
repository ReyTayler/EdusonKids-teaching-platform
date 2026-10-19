"""Шаги импорта Eduson по порядку: people → groups → lessons (→ продления, сверка).

Каждый шаг — функция (src, ctx): читает EdusonSource, пишет модели KOTOKOD и
заполняет соответствия id в ImportContext для следующих шагов. Транзакцию и
контекст журнала изменений открывает команда import_eduson, не шаги.
"""
from __future__ import annotations

from django.db import connection

from apps.eduson_import.context import ImportDataError


def require_atomic(step: str) -> None:
    """Шаг пишет десятки связанных строк; вне транзакции падение на середине
    оставило бы полуимпорт, который повторный запуск уже не примет (база не пуста).
    Транзакцию открывает команда — шаг только проверяет, что она есть."""
    if not connection.in_atomic_block:
        raise ImportDataError(f'шаг «{step}» вызван вне транзакции — запускайте импорт командой import_eduson')
