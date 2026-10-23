"""
Запросы справочника причин отмены и запись об отмене урока.

Справочник маленький (единицы строк на сторону) и ограничен экраном настроек,
поэтому список читается целиком одним запросом, без пагинации — осознанное
исключение, как у стадий продлений.
"""
from __future__ import annotations

from django.db.models import Max, Value
from django.db.models.functions import Lower
from django.utils import timezone

from apps.cancellations.models import CancellationReason, CancellationSide, LessonCancellation
from apps.cancellations.rules import CancellationDraft, is_compensated

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
    имени и запись видят одно и то же состояние этой строки.

    FOR NO KEY UPDATE: ключ причины не меняется, а INSERT записи об отмене
    берёт на строку причины FOR KEY SHARE (FK) — с NO KEY UPDATE они
    совместимы, и правка справочника не тормозит отмены уроков."""
    return _active().select_for_update(no_key=True).filter(pk=pk).first()


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


def create_for_marker(marker, draft: CancellationDraft) -> LessonCancellation:
    """Запись об отмене для только что вставленного маркера.

    Вызывается из scheduling.repository.cancel_lesson ВНУТРИ его транзакции:
    после лока курса и INSERT маркера, до пересчёта начисления — порядок
    «план → запись отмены → деньги». compensated — снимок решения по дате
    маркера (спека §4); teacher — эффективный преподаватель маркера."""
    return LessonCancellation.objects.create(
        group_id=marker.group_id, lesson_date=marker.scheduled_date,
        scheduled_time=marker.scheduled_time, teacher_id=marker.teacher_id,
        planned_lesson=marker, side=draft.side, reason_id=draft.reason_id,
        reason_text=draft.other_text if draft.is_other else draft.reason_name,
        is_other=draft.is_other, notified_on=draft.notified_on,
        compensated=is_compensated(
            side=draft.side, is_other=draft.is_other,
            reason_compensation=draft.reason_compensation,
            notified_on=draft.notified_on, lesson_date=marker.scheduled_date,
        ),
        created_by_id=draft.created_by_id,
    )


def soft_delete(pk: int) -> bool:
    """Мягкое удаление одним UPDATE; False — причины нет или она уже удалена.

    QuerySet.update() обходит auto_now, поэтому updated_at ставим явно."""
    now = timezone.now()
    return _active().filter(pk=pk).update(deleted_at=now, updated_at=now) > 0
