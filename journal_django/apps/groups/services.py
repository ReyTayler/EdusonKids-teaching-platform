"""
GroupsService — тонкий слой бизнес-логики между views и repository.

Принцип: никакого SQL здесь — всё через repository.
Бизнес-правила (409 при дубле имени) обрабатываются во view.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from rest_framework.exceptions import ValidationError

from apps.groups import repository

logger = logging.getLogger(__name__)


def list_groups(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = 'name',
    sort_dir: str = 'asc',
    filters: Optional[dict] = None,
    include_inactive: bool = False,
) -> dict:
    """Делегирует список групп в repository (архив скрыт по умолчанию)."""
    return repository.list_groups(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filters=filters,
        include_inactive=include_inactive,
    )


def get_group(group_id: int) -> Optional[dict]:
    """Возвращает группу или None."""
    return repository.get_group(group_id)


def get_group_progress(group_id: int) -> Optional[dict]:
    """Матрица посещаемости группы для вкладки «Прогресс», или None."""
    return repository.get_group_progress(group_id)


def create_group(data: dict) -> dict:
    """
    Создаёт группу.

    Поднимает ValidationError(409-style) при нарушении UNIQUE по имени.
    (pgcode 23505 — unique_violation)
    """
    from django.db import IntegrityError
    try:
        group = repository.create_group(data)
    except IntegrityError as exc:
        # pg error code 23505 = unique_violation
        if _is_unique_violation(exc):
            raise ValidationError({'error': 'Already exists'}, code='conflict')
        raise
    _autogenerate_plan(group['id'], 'group_create')
    return group


def update_group(group_id: int, data: dict) -> Optional[dict]:
    """Обновляет группу. Возвращает None если не найдена.

    Если сменилась ручная длина курса (lessons_total) — план подгоняется под неё
    в ТОЙ ЖЕ транзакции: 409 (PlanHasRecordedLessons) откатывает и запись поля,
    иначе в БД осталось бы число, которому план не соответствует.
    """
    from django.db import transaction
    from apps.groups.models import Group
    from apps.scheduling import services as scheduling_services

    before = (
        Group.objects.filter(id=group_id)
        .values_list('lessons_total', flat=True)
        .first()
    )
    with transaction.atomic():
        group = repository.update_group(group_id, data)
        if group is None:
            return None
        if 'lessons_total' in data and data['lessons_total'] != before:
            scheduling_services.resize_plan(group_id)

    _autogenerate_plan(group_id, 'group_update')
    return group


def soft_delete_group(group_id: int) -> bool:
    """Мягкое удаление (active=false). Возвращает False если не найдена."""
    return repository.soft_delete_group(group_id)


# ---------------------------------------------------------------------------
# Расписание (Ф3): версионные слоты
# ---------------------------------------------------------------------------

def get_schedule(group_id: int) -> Optional[dict]:
    """Расписание группы (слоты с датами действия) или None."""
    return repository.get_schedule(group_id)


def apply_schedule_change(group_id: int, data: dict) -> Optional[dict]:
    """Задать/сменить расписание группы (endpoint schedule-change) — единая точка
    первичной настройки расписания (кнопка «Задать расписание» на карточке группы).

    Помимо версионной вставки слотов (repository) держит согласованными
    производные поля группы:
      - lessons_per_week = число слотов;
      - при первичной настройке (group_start_date ещё NULL) проставляет дату
        начала = дате начала расписания (update_group ставит её только NULL→значение,
        уже заданную не трогает).
    Затем автоген плана. None, если группы нет.

    ВАЖНО: enhance только в СЕРВИСЕ (endpoint), не в repository.apply_schedule_change,
    который переиспользуют внутренние scheduling-флоу (permanent-change/resume) —
    те сами ведут lessons_per_week и не должны трогать group_start_date."""
    result = repository.apply_schedule_change(
        group_id, data['effective_from'], data['slots'],
    )
    if result is None:
        return None
    repository.update_group(group_id, {
        'lessons_per_week': len(data['slots']),
        'group_start_date': data['effective_from'],
    })
    _autogenerate_plan(group_id, 'schedule_change')
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _autogenerate_plan(group_id: int, source: str) -> None:
    """Механизм 1: авто-генерация плана при первичной настройке группы (появились
    старт+слот). Прямой синхронный вызов ПОСЛЕ коммита repository (repository держит
    свой atomic; ATOMIC_REQUESTS=False → выход из него = commit, данные видны).
    Сигналы не используем: слоты создаются bulk_create → post_save не летит.

    Best-effort: ошибки не пробрасываем — авто-генерация не должна ронять
    создание/правку группы (guard/идемпотентность/аудит — в оркестраторе)."""
    from apps.scheduling import services as scheduling_services  # локальный импорт (цикл groups↔scheduling)
    try:
        scheduling_services.autogenerate_plan_on_setup(group_id)
    except Exception:  # noqa: BLE001 — side-effect не должен ронять запрос
        logger.exception('autogenerate plan failed for group %s (%s)', group_id, source)

def _is_unique_violation(exc: Exception) -> bool:
    """Проверить, является ли IntegrityError нарушением уникальности (pgcode 23505)."""
    # psycopg2 кладёт pgcode в .pgcode или в .__cause__.pgcode
    pgcode: Any = getattr(exc, 'pgcode', None)
    if pgcode == '23505':
        return True
    cause = getattr(exc, '__cause__', None)
    if cause and getattr(cause, 'pgcode', None) == '23505':
        return True
    return False
