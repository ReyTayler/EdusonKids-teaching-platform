"""
DirectionsService — тонкий слой между views и repository.

Никакого SQL здесь — всё через repository.
"""
from __future__ import annotations

from typing import Optional

from apps.directions import repository


def list_directions(include_inactive: bool = False) -> list[dict]:
    """Делегирует список направлений в repository."""
    return repository.list_directions(include_inactive=include_inactive)


def get_direction(direction_id: int) -> Optional[dict]:
    """Возвращает направление или None."""
    return repository.get_direction(direction_id)


def create_direction(data: dict) -> dict:
    """Создаёт направление. 409 при UniqueViolation поднимает view."""
    return repository.create_direction(data)


def update_direction(direction_id: int, data: dict) -> Optional[dict]:
    """Обновляет направление. Возвращает None если не найдено."""
    return repository.update_direction(direction_id, data)


def soft_delete_direction(direction_id: int) -> bool:
    """Мягкое удаление (active=false). Возвращает False если не найдено."""
    return repository.soft_delete_direction(direction_id)


def get_direction_payments_count(direction_id: int) -> int:
    """Количество оплат для направления — для проверки перед DELETE."""
    return repository.get_direction_payments_count(direction_id)
