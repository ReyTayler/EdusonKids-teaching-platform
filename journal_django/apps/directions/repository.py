"""
DirectionsRepository — единственное место доступа к данным раздела directions.

ORM-порт логики services/repo/directions.js (раздел 09). Контракт сохранён:
list[dict] / dict|None / bool / int, формы ответа не меняются.

Особенность update_direction: subscription_price имеет CASE-логику —
  присутствует ключ → перезаписывается (включая null); отсутствует → не трогаем.

DELETE: перед soft-delete надо проверить наличие payments (get_direction_payments_count).
"""
from __future__ import annotations

from typing import Optional

from apps.core.utils.orm import dictrow, dictrows
from apps.payments.models import Payment

from .models import Direction


# ---------------------------------------------------------------------------
# Repository functions (ORM-порт services/repo/directions.js)
# ---------------------------------------------------------------------------

def list_directions(include_inactive: bool = False) -> list[dict]:
    """
    Возвращает список направлений.

    ORM-эквивалент: SELECT * FROM directions [WHERE active=true] ORDER BY name
    """
    qs = Direction.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return dictrows(qs.order_by('name').values())


def get_direction(direction_id: int) -> Optional[dict]:
    """Возвращает одно направление или None (SELECT * FROM directions WHERE id=%s)."""
    return dictrow(Direction.objects.filter(id=direction_id).values())


def create_direction(data: dict) -> dict:
    """
    Создаёт направление (INSERT ... RETURNING *).

    NULLIF(color,'') → пустая строка трактуется как None (паттерн 4.5).
    UniqueViolation по name бросает IntegrityError — обрабатывает view (409).
    """
    obj = Direction.objects.create(
        name=data['name'],
        total_lessons=data.get('total_lessons'),
        color=data.get('color') or None,           # NULLIF($5,'')
        subscription_price=data.get('subscription_price'),
    )
    return dictrow(Direction.objects.filter(pk=obj.pk).values())


def update_direction(direction_id: int, data: dict) -> Optional[dict]:
    """
    Обновляет направление (PATCH-семантика, дословно из directions.js).

    Семантика по полям (повторяет COALESCE/NULLIF/CASE исходника):
    - name: COALESCE(%s, col), %s = value or None → set только если непусто.
    - active: COALESCE(%s, col) с sentinel "ключ присутствует" →
      set если ключ есть и значение не None (включая False).
    - total_lessons: COALESCE(%s, col) → set если ключ есть и значение не None.
    - color: COALESCE(NULLIF(%s,''), col) → set если ключ есть и значение непусто.
    - subscription_price: CASE WHEN has_key THEN value ELSE keep → перезаписывается
      всегда при наличии ключа (включая null).
    """
    obj = Direction.objects.filter(id=direction_id).first()
    if obj is None:
        return None

    if data.get('name'):
        obj.name = data['name']
    if data.get('active') is not None and 'active' in data:
        obj.active = data['active']
    if data.get('total_lessons') is not None and 'total_lessons' in data:
        obj.total_lessons = data['total_lessons']
    if data.get('color'):
        obj.color = data['color']
    if 'subscription_price' in data:
        obj.subscription_price = data['subscription_price']

    obj.save()
    return dictrow(Direction.objects.filter(id=direction_id).values())


def soft_delete_direction(direction_id: int) -> bool:
    """Мягкое удаление: active=false. True если строка найдена и обновлена."""
    updated = Direction.objects.filter(id=direction_id).update(active=False)
    return updated > 0


def get_direction_payments_count(direction_id: int) -> int:
    """
    Количество оплат для направления.

    ORM-эквивалент: SELECT COUNT(*)::int FROM payments WHERE direction_id=%s
    Используется в DELETE перед soft-delete (409 если есть оплаты).
    """
    return Payment.objects.filter(direction_id=direction_id).count()
