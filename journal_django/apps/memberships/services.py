"""
MembershipsService — тонкий слой бизнес-логики между views и repository.

Принцип: никакого SQL здесь — всё через repository.
"""
from __future__ import annotations

from typing import Optional

from django.db import transaction

from apps.memberships import repository


def _cancel_membership_extra_lessons(membership_id: int) -> None:
    """Гейт доп.уроков при снятии членства: блок при назначенных, авто-удаление
    pending (см. apps.extra_lessons.services.enforce_membership_cancellation).
    No-op, если membership не найден (снятие всё равно вернёт «не найдено»)."""
    from apps.extra_lessons import services as extra_lessons_services

    row = repository.get_student_group(membership_id)
    if row is not None:
        extra_lessons_services.enforce_membership_cancellation(
            row['student_id'], row['group_id'])


def list_memberships(
    group_id: Optional[int] = None,
    student_id: Optional[int] = None,
    include_inactive: bool = False,
) -> list[dict]:
    """Делегирует список membership в repository."""
    return repository.list_memberships(
        group_id=group_id,
        student_id=student_id,
        include_inactive=include_inactive,
    )


def add_membership(data: dict) -> dict:
    """Создаёт или реактивирует membership (UPSERT)."""
    return repository.add_membership(data)


@transaction.atomic
def update_membership(membership_id: int, data: dict) -> Optional[dict]:
    """Обновляет membership. Возвращает None если не найден.

    Деактивация (active=false) = снятие членства → гейт доп.уроков (блок при
    назначенных, авто-удаление pending) в той же транзакции ДО записи."""
    if data.get('active') is False:
        _cancel_membership_extra_lessons(membership_id)
    return repository.update_membership(membership_id, data)


@transaction.atomic
def remove_membership(membership_id: int) -> bool:
    """Мягкое удаление (active=false) = снятие членства. Гейт доп.уроков (блок при
    назначенных, авто-удаление pending) в той же транзакции ДО записи. False если
    не найден."""
    _cancel_membership_extra_lessons(membership_id)
    return repository.remove_membership(membership_id)


@transaction.atomic
def transfer_membership(membership_id: int, to_group_id: int) -> Optional[dict]:
    """Переводит ученика в другую группу того же направления (см. repository.transfer_membership).

    Перевод снимает членство в СТАРОЙ группе → гейт доп.уроков старой группы (блок
    при назначенных, авто-удаление pending) в той же транзакции ДО перевода."""
    _cancel_membership_extra_lessons(membership_id)
    return repository.transfer_membership(membership_id, to_group_id)


