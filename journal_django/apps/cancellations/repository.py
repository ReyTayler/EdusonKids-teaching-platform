"""
Запросы справочника причин отмены.

Справочник маленький (единицы строк на сторону) и ограничен экраном настроек,
поэтому список читается целиком одним запросом, без пагинации — осознанное
исключение, как у стадий продлений.
"""
from __future__ import annotations

from django.db.models import Max, Value
from django.db.models.functions import Lower
from django.utils import timezone

from apps.cancellations.models import CancellationReason, CancellationSide

# Поля строки справочника для API; compensation вырезает services.serialize_reason
# для не-superadmin.
ROW_FIELDS = ('id', 'side', 'name', 'position', 'compensation')


def _active():
    return CancellationReason.objects.filter(deleted_at__isnull=True)


def list_active() -> dict[str, list[CancellationReason]]:
    """Активные причины по сторонам, ORDER BY position, id — один запрос.

    id — детерминированный тай-брейк: позиции двух причин могут совпасть
    (параллельное создание или ручной PATCH position)."""
    out: dict[str, list[CancellationReason]] = {s.value: [] for s in CancellationSide}
    for reason in _active().only(*ROW_FIELDS).order_by('position', 'id'):
        out[reason.side].append(reason)
    return out


def get_active(pk: int) -> CancellationReason | None:
    return _active().filter(pk=pk).first()


def get_active_for_update(pk: int) -> CancellationReason | None:
    """Под локом строки: правки одной причины сериализуются, проверка дубля
    имени и запись видят одно и то же состояние этой строки."""
    return _active().select_for_update().filter(pk=pk).first()


def active_name_taken(side: str, name: str, *, exclude_id: int | None = None) -> bool:
    """Есть ли среди активных причин стороны такое же имя без учёта регистра.

    Предпроверка ради понятного текста ошибки; гонку двух одинаковых вставок
    ловит частичный UNIQUE cancel_reason_active_name_uq (services переводит
    его в тот же текст)."""
    # Lower(), а не iexact (UPPER): сравнение обязано совпадать с выражением
    # UNIQUE (models.py) — для символов, где UPPER и LOWER расходятся, иначе
    # предпроверка и БД давали бы разные ответы.
    qs = (_active().annotate(_lname=Lower('name'))
          .filter(side=side, _lname=Lower(Value(name))))
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


def next_position(side: str) -> int:
    """Позиция новой причины — в конец стороны (0 для пустой).

    Две параллельные вставки могут получить одну позицию — это безвредно:
    порядок списка добивается тай-брейком по id."""
    top = _active().filter(side=side).aggregate(top=Max('position'))['top']
    return 0 if top is None else top + 1


def create(*, side: str, name: str, compensation: bool) -> CancellationReason:
    return CancellationReason.objects.create(
        side=side, name=name, compensation=compensation, position=next_position(side),
    )


def soft_delete(pk: int) -> bool:
    """Мягкое удаление одним UPDATE; False — причины нет или она уже удалена.

    QuerySet.update() обходит auto_now, поэтому updated_at ставим явно."""
    now = timezone.now()
    return _active().filter(pk=pk).update(deleted_at=now, updated_at=now) > 0
