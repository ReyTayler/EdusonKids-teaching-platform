"""
DiscountsRepository — единственное место доступа к данным раздела discounts.

ORM-порт services/repo/discounts.js (раздел 09).

Поля таблицы (011_discounts.sql):
  id, name, amount (numeric(5,4), 0..1), active, created_at

Контракт списка: без пагинации, ORDER BY active DESC, name.
"""
from __future__ import annotations

from typing import Optional

from django.db.models.functions import Now

from apps.core.utils.orm import dictrow, dictrows

from .models import Discount


# ---------------------------------------------------------------------------
# Repository functions (ORM-порт services/repo/discounts.js)
# ---------------------------------------------------------------------------

def list_discounts(include_inactive: bool = False) -> list[dict]:
    """SELECT * FROM discounts [WHERE active=true] ORDER BY active DESC, name."""
    qs = Discount.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return dictrows(qs.order_by('-active', 'name').values())


def get_discount(discount_id: int) -> Optional[dict]:
    """SELECT * FROM discounts WHERE id=%s."""
    return dictrow(Discount.objects.filter(id=discount_id).values())


def create_discount(data: dict) -> dict:
    """
    Создаёт скидку (INSERT (name, amount) RETURNING *).

    active по умолчанию true (DB), created_at — DB DEFAULT now() через Now().
    """
    obj = Discount.objects.create(
        name=data['name'],
        amount=data['amount'],
        created_at=Now(),
    )
    return dictrow(Discount.objects.filter(pk=obj.pk).values())


def update_discount(discount_id: int, data: dict) -> Optional[dict]:
    """
    Обновляет скидку (PATCH через COALESCE, дословно из discounts.js).

    - name:   COALESCE(%s, name)   → set если непусто.
    - amount: COALESCE(%s, amount) → set если ключ есть и значение не None (вкл. 0).
    - active: COALESCE(%s, active) → set если ключ есть и значение не None.
    """
    obj = Discount.objects.filter(id=discount_id).first()
    if obj is None:
        return None

    if data.get('name'):
        obj.name = data['name']
    if data.get('amount') is not None and 'amount' in data:
        obj.amount = data['amount']
    if data.get('active') is not None and 'active' in data:
        obj.active = data['active']

    obj.save()
    return dictrow(Discount.objects.filter(id=discount_id).values())


def soft_delete_discount(discount_id: int) -> bool:
    """Мягкое удаление: active=false. True если строка найдена."""
    updated = Discount.objects.filter(id=discount_id).update(active=False)
    return updated > 0
