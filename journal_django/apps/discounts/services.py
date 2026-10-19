"""
DiscountsService — тонкий слой между views и repository.

Никакого SQL здесь — всё через repository.
"""
from __future__ import annotations

from typing import Optional

from apps.discounts import repository


def list_discounts(include_inactive: bool = False) -> list[dict]:
    """Делегирует список скидок в repository."""
    return repository.list_discounts(include_inactive=include_inactive)


def get_discount(discount_id: int) -> Optional[dict]:
    """Возвращает скидку или None."""
    return repository.get_discount(discount_id)


def create_discount(data: dict) -> dict:
    """Создаёт скидку."""
    return repository.create_discount(data)


def update_discount(discount_id: int, data: dict) -> Optional[dict]:
    """Обновляет скидку. Возвращает None если не найдена."""
    return repository.update_discount(discount_id, data)


def soft_delete_discount(discount_id: int) -> bool:
    """Мягкое удаление (active=false). Возвращает False если не найдена."""
    return repository.soft_delete_discount(discount_id)
