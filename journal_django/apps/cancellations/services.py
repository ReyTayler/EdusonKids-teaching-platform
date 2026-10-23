"""
Доменные операции справочника причин отмены (спека 2026-10-23, §3.1, §5).

Флаг compensation решает, заплатят ли преподавателю за отмену, то есть
относится к мотивации: его видит и меняет только superadmin (правило владельца
2026-10-23, В3 плана). Остальные роли получают строку без этого ключа.
"""
from __future__ import annotations

from typing import Optional

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.cancellations import repository
from apps.cancellations.models import CancellationReason

DUPLICATE_NAME = 'Такая причина уже есть.'
NOTHING_TO_CHANGE = 'Нечего менять.'
_ACTIVE_NAME_CONSTRAINT = 'cancel_reason_active_name_uq'


class CancellationInvalid(ValueError):
    """Бизнес-ошибка отмены урока (причина не найдена, другая сторона…) → 400."""


def serialize_reason(reason: CancellationReason, *, with_compensation: bool) -> dict:
    row = {'id': reason.id, 'side': reason.side, 'name': reason.name,
           'position': reason.position}
    if with_compensation:
        row['compensation'] = reason.compensation
    return row


def list_reasons(*, with_compensation: bool) -> dict[str, list[dict]]:
    return {side: [serialize_reason(r, with_compensation=with_compensation) for r in rows]
            for side, rows in repository.list_active().items()}


def _constraint_name(exc: IntegrityError) -> Optional[str]:
    diag = getattr(exc.__cause__, 'diag', None)
    return getattr(diag, 'constraint_name', None)


def _duplicate() -> ValidationError:
    return ValidationError({'name': [DUPLICATE_NAME]})


def _write_guarded(write):
    """Запись под savepoint: гонка двух одинаковых имён (предпроверка обеих
    прошла) упирается в частичный UNIQUE → тот же 400, что и предпроверка.
    Чужое нарушение — дальше: 500 честнее неверного объяснения."""
    try:
        with transaction.atomic():
            return write()
    except IntegrityError as exc:
        if _constraint_name(exc) == _ACTIVE_NAME_CONSTRAINT:
            raise _duplicate() from exc
        raise


def create_reason(*, side: str, name: str, compensation: bool = False) -> CancellationReason:
    if repository.active_name_taken(side, name):
        raise _duplicate()
    return _write_guarded(
        lambda: repository.create(side=side, name=name, compensation=compensation))


def update_reason(pk: int, fields: dict) -> CancellationReason | None:
    """PATCH имени/флага/позиции. None — причины нет или она удалена (404).
    Сторона неизменяема: её не принимает сериализатор.

    Пустой набор полей — ошибка, а не no-op: save() записал бы updated_at, и в
    журнале изменений осталась бы операция без смысла."""
    if not fields:
        raise CancellationInvalid(NOTHING_TO_CHANGE)
    with transaction.atomic():
        reason = repository.get_active_for_update(pk)
        if reason is None:
            return None
        name = fields.get('name')
        if name is not None and repository.active_name_taken(reason.side, name, exclude_id=pk):
            raise _duplicate()
        for key, value in fields.items():
            setattr(reason, key, value)

        def _save():
            reason.save(update_fields=[*fields.keys(), 'updated_at'])
            return reason

        return _write_guarded(_save)


def delete_reason(pk: int) -> bool:
    """Мягкое удаление: из формы причина пропадает, записи об отменах хранят
    ссылку (FK PROTECT) и снимок текста. False — нет или уже удалена (404)."""
    return repository.soft_delete(pk)
