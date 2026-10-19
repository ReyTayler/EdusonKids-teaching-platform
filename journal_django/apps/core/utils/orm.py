"""
ORM helpers для переходного периода SQL → Django ORM (раздел 09).

dictrows / dictrow — единообразно отдают результат `.values()` как обычные
list[dict] / dict|None, заменяя ручные `_dictfetchall` / `_dictfetchone`
из repository.py. Контракт репозиториев (list[dict] / dict|None) сохраняется
байт-в-байт: ORM `.values()` отдаёт ровно те же Python-типы (datetime.date,
Decimal), которые потом приводит DateSafeJSONRenderer.

Использование:
    from apps.core.utils.orm import dictrows, dictrow

    def list_x() -> list[dict]:
        return dictrows(X.objects.order_by('name').values())

    def get_x(pk) -> dict | None:
        return dictrow(X.objects.filter(id=pk).values())
"""
from __future__ import annotations

from typing import Optional


def dictrows(values_qs) -> list[dict]:
    """
    Материализует `.values()` / `.values(...)` QuerySet в list[dict].

    Принимает ValuesQuerySet (результат `.values(...)`); каждый элемент уже dict.
    Возвращает обычный list обычных dict (а не ValuesQuerySet/ValuesIterable),
    чтобы поведение совпадало с прежним `_dictfetchall`.
    """
    return [dict(row) for row in values_qs]


def dictrow(values_qs) -> Optional[dict]:
    """
    Возвращает первую строку `.values(...)` QuerySet как dict, либо None.

    Эквивалент прежнего `_dictfetchone`. Вызывающий передаёт уже отфильтрованный
    QuerySet (`.filter(...).values(...)`); срез `[:1]` ограничивает выборку.
    """
    for row in values_qs[:1]:
        return dict(row)
    return None
