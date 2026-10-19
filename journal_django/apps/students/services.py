"""
StudentsService — тонкий слой между views и repository.

Принцип: никакого SQL здесь — всё через repository.
"""
from __future__ import annotations

from typing import Optional

from django.db import transaction

from apps.payments import services as payments_services
from apps.students import repository


def list_students(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = 'full_name',
    sort_dir: str = 'asc',
    filters: Optional[dict] = None,
) -> dict:
    """Делегирует список учеников в repository."""
    return repository.list_students(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filters=filters,
    )


def get_student(student_id: int) -> Optional[dict]:
    """Возвращает ученика или None."""
    return repository.get_student(student_id)


def student_exists(student_id: int) -> bool:
    """Факт существования ученика — для 404-гейтов, без выборки полей и стадии."""
    return repository.student_exists(student_id)


def create_student(data: dict) -> dict:
    """Создаёт ученика."""
    return repository.create_student(data)


def update_student(student_id: int, data: dict) -> Optional[dict]:
    """Обновляет ученика. Возвращает None если не найден."""
    return repository.update_student(student_id, data)


def student_stats(student_id: int) -> dict:
    """Сводка посещаемости ученика."""
    return repository.student_stats(student_id)


def get_student_balance(student_id: int) -> dict:
    """Баланс ученика по направлениям (постоянный дом — apps/payments/)."""
    return payments_services.get_student_balance(student_id)


def add_comment(student_id: int, body: str, author_id: Optional[int]):
    """Создаёт комментарий к ученику."""
    return repository.add_comment(student_id, body, author_id)


def delete_comment(student_id: int, comment_id: int) -> bool:
    """Удаляет комментарий. False если не найден."""
    return repository.delete_comment(student_id, comment_id)


@transaction.atomic
def set_student_manager(student_id: int, manager_id: Optional[int], *, actor=None) -> Optional[dict]:
    """
    Сменить ответственного менеджера ученика и синхронно переписать assignee
    АКТИВНОЙ (открытой) сделки продления этого ученика — единый источник правды
    вместо независимого назначения на сделке. Закрытые (won/lost) сделки
    сохраняют своего исторического ответственного и не трогаются. Возвращает
    None, если ученика нет; ValueError, если manager_id указывает на
    неподходящую учётку (не manager/admin/superadmin или неактивна).

    actor принят на будущее (например, если появится RenewalActivity для смены
    менеджера), но пока не используется — атрибуция pghistory для этого изменения
    уже берётся из контекста middleware запроса, не из этого параметра.
    """
    from apps.accounts.models import Account
    from apps.renewals.models import RenewalDeal
    from apps.students.models import Student

    student = Student.objects.filter(id=student_id).first()
    if student is None:
        return None

    if manager_id is not None:
        # Тот же критерий, что apps.renewals.services.list_assignees() —
        # кандидат в ответственные по сделкам продления.
        is_eligible = Account.objects.filter(
            id=manager_id, role__in=['manager', 'admin', 'superadmin'], is_active=True,
        ).exists()
        if not is_eligible:
            raise ValueError('manager account not found or not eligible')

    student.manager_id = manager_id
    student.save(update_fields=['manager'])
    RenewalDeal.objects.filter(
        student_id=student_id, outcome_at__isnull=True,
    ).update(assignee_id=manager_id)

    return repository.get_student(student_id)
