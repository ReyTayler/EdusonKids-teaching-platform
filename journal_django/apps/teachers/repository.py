"""
TeachersRepository — единственное место доступа к данным раздела teachers.

ORM-порт services/repo/teachers.js (раздел 09). Контракт ответа списка —
plain list (не пагинированный), как в оригинале.
"""
from __future__ import annotations

from typing import Optional

from django.db.models.functions import Now

from apps.core.utils.orm import dictrow, dictrows
from apps.notifications.models import TelegramRecipient

from .models import Teacher


# ---------------------------------------------------------------------------
# Repository functions (ORM-порт services/repo/teachers.js)
# ---------------------------------------------------------------------------

def _telegram_by_teacher_id(teacher_ids: list[int]) -> dict[int, dict]:
    """
    Привязки Telegram одним запросом (без N+1), keyed by teacher_id.

    Карточка преподавателя отдельного эндпоинта не имеет — привязка
    подмешивается прямо в list/get teachers.
    """
    rows = (
        TelegramRecipient.objects
        .filter(teacher_id__in=teacher_ids)
        .select_related('telegram_user')
    )
    return {
        recipient.teacher_id: {
            'chat_id': recipient.telegram_user.chat_id,
            'username': recipient.telegram_user.username,
            'full_name': recipient.telegram_user.full_name,
            'is_active': recipient.is_active,
            'blocked_reason': recipient.blocked_reason,
        }
        for recipient in rows
    }


def list_teachers(include_inactive: bool = False) -> list[dict]:
    """SELECT * FROM teachers [WHERE active=true] ORDER BY name."""
    qs = Teacher.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    rows = dictrows(qs.order_by('name').values())
    telegram_by_id = _telegram_by_teacher_id([row['id'] for row in rows])
    for row in rows:
        row['telegram'] = telegram_by_id.get(row['id'])
    return rows


def get_teacher(teacher_id: int) -> Optional[dict]:
    """SELECT * FROM teachers WHERE id=%s."""
    row = dictrow(Teacher.objects.filter(id=teacher_id).values())
    if row is None:
        return None
    row['telegram'] = _telegram_by_teacher_id([teacher_id]).get(teacher_id)
    return row


def create_teacher(data: dict) -> dict:
    """
    Создаёт преподавателя (INSERT ... RETURNING *).

    NULLIF(email,''), NULLIF(phone,'') → пустая строка → None (паттерн 4.5).
    created_at: модель не объявляет default, поэтому передаём DB-функцию Now()
    (= now()), повторяя серверный DEFAULT исходного INSERT; значение возвращаем
    через refetch .values().
    """
    obj = Teacher.objects.create(
        name=data['name'],
        email=data.get('email') or None,
        phone=data.get('phone') or None,
        created_at=Now(),
    )
    return dictrow(Teacher.objects.filter(pk=obj.pk).values())


def update_teacher(teacher_id: int, data: dict) -> Optional[dict]:
    """
    Обновляет преподавателя (PATCH через COALESCE/NULLIF, дословно из teachers.js).

    - name:  COALESCE(%s, name)              → set если непусто.
    - email: COALESCE(NULLIF(%s,''), email)  → set если ключ есть и значение непусто.
    - phone: COALESCE(NULLIF(%s,''), phone)  → set если ключ есть и значение непусто.
    - active: COALESCE(%s, active)           → set если ключ есть и значение не None.
    """
    obj = Teacher.objects.filter(id=teacher_id).first()
    if obj is None:
        return None

    if data.get('name'):
        obj.name = data['name']
    if data.get('email'):              # NULLIF: пустая строка/None → не трогаем
        obj.email = data['email']
    if data.get('phone'):
        obj.phone = data['phone']
    if data.get('active') is not None and 'active' in data:
        obj.active = data['active']

    obj.save()
    return dictrow(Teacher.objects.filter(id=teacher_id).values())


def soft_delete_teacher(teacher_id: int) -> bool:
    """Мягкое удаление: active=false. True если строка найдена."""
    updated = Teacher.objects.filter(id=teacher_id).update(active=False)
    return updated > 0
