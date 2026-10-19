"""
PaymentsRepository — единственное место доступа к данным раздела payments.

ORM-порт services/repo/payments.js (раздел 09).

Критичный паритет:
  • unit_price / total_amount — сырые Decimal → renderer выдаёт строки (как Express).
  • purchased/attended/balance/total_paid/new_balance — числа (int|float) через
    _js_number() (живёт в apps/finances).
  • Транзакция + FOR UPDATE в create_payment (select_for_update).
  • payments immutable: только create/delete, никакого UPDATE.
  • unit_price округляется до копеек ДО умножения (round_kopecks).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import F, Sum, Value
from django.db.models.functions import Coalesce, Now

from apps.core.utils.decimal import round_kopecks
from apps.core.utils.orm import dictrow, dictrows

from .models import Payment


# Поля строки оплаты (p.* / RETURNING *), в порядке схемы.
# Whitelist колонок для списков оплат. Новое поле модели, не добавленное сюда,
# молча не доедет до фронта: так доплата к абонементу приезжала без привязки
# (parent_payment_id/subscription_index) и интерфейс не мог пересчитать цену блока.
_PAYMENT_FIELDS = (
    'id', 'student_id', 'direction_id', 'subscriptions_count', 'lessons_count', 'kind', 'unit_price',
    'total_amount', 'paid_at', 'note', 'created_at', 'created_by',
    'parent_payment_id', 'subscription_index',
)

# _js_number и расчёт баланса живут в едином доме apps/finances.
# Переэкспортируем для обратной совместимости (delete_payment, тесты).
from apps.finances.repository import _js_number  # noqa: E402,F401


def _normalize_lessons_count(row: dict) -> dict:
    """
    lessons_count теперь DecimalField (Task 4.0: дробные возвраты, -3.5 и т.п.),
    но в JSON-ответе должен остаться JS Number (int для целых покупок), как раньше
    при IntegerField — иначе DateSafeJSONRenderer превратит его в строку '2.0',
    как unit_price/total_amount, что ломает контракт API.
    """
    if row.get('lessons_count') is not None:
        row['lessons_count'] = _js_number(row['lessons_count'])
    return row


# ---------------------------------------------------------------------------
# Repository functions (ORM-порт services/repo/payments.js)
# ---------------------------------------------------------------------------

def create_payment(data: dict) -> dict:
    """
    Создаёт оплату внутри транзакции с SELECT FOR UPDATE.

    Возвращает {'payment': row} или {'error': ...} — НЕ кидает исключение.
    Порядок (инвариант): лок direction → подсчёт already под локом → проверка cap → INSERT.
    """
    from apps.directions.models import Direction

    if data.get('kind') == 'surcharge':
        return _create_surcharge(data)

    student_id = data['student_id']
    direction_id = data['direction_id']
    lessons_count = data['lessons_count']
    total_amount = data['total_amount']
    paid_at = data['paid_at']
    note = data.get('note')
    created_by = data.get('created_by')
    # kind='extra' — доплата за доп.урок СВЕРХ курса: деньги в реальном направлении,
    # но МИМО лимита (cap не проверяем и в него не входим). Остальное — как purchase.
    kind = data.get('kind') or 'purchase'

    with transaction.atomic():
        dir_row = (
            Direction.objects.select_for_update()
            .filter(id=direction_id)
            .values('id', 'total_lessons')
            .first()
        )
        if dir_row is None:
            return {'error': 'direction_not_found'}
        if not dir_row['total_lessons'] or dir_row['total_lessons'] <= 0:
            return {'error': 'no_capacity'}

        # cap в уроках: покупки этого направления МИНУС возвращённые уроки
        # (строки kind='refund' отрицательные — SUM вычитает их сам). Без возвратов
        # лимит оставался бы занятым и вернувшийся ученик не смог бы оплатить курс
        # заново, хотя деньги за него уже вернули (см. refund_student).
        # Доплата сверх курса (kind='extra') лимитом не ограничена и в него не входит —
        # её возврат пишется без направления и сюда тоже не попадает.
        if kind != 'extra':
            already = (
                Payment.objects
                .filter(student_id=student_id, direction_id=direction_id,
                        kind__in=('purchase', 'refund'))
                .aggregate(s=Coalesce(Sum('lessons_count'), Value(Decimal('0'))))['s']
            )
            if already + lessons_count > dir_row['total_lessons']:
                return {
                    'error': 'cap_exceeded',
                    'already': _js_number(already),
                    'cap_subscriptions': int(dir_row['total_lessons'] // 4),
                }

        total = round_kopecks(total_amount)
        unit_price = round_kopecks(total / Decimal(lessons_count))
        subs = lessons_count // 4 if lessons_count % 4 == 0 else None

        obj = Payment.objects.create(
            student_id=student_id,
            direction_id=direction_id,
            subscriptions_count=subs,
            lessons_count=lessons_count,
            kind=kind,
            unit_price=unit_price,
            total_amount=total,
            paid_at=paid_at,
            note=note or None,
            created_by=created_by or None,
            created_at=Now(),
        )
        row = _normalize_lessons_count(dictrow(Payment.objects.filter(pk=obj.pk).values()))

    return {'payment': row}


def _create_surcharge(data: dict) -> dict:
    """
    Доплата к абонементу: деньги без уроков, поднимающие цену конкретного блока.

    Лимит курса не трогаем (уроков не добавляем), направление берём у родителя —
    иначе доплата не попала бы в фильтры отчётов по направлению. Номер блока
    проверяем здесь: в CHECK его не выразить (зависит от строки-родителя).
    """
    from apps.finances.lots import LESSONS_PER_SUBSCRIPTION

    parent = (
        Payment.objects
        .filter(id=data['parent_payment_id'])
        .values('id', 'student_id', 'direction_id', 'lessons_count', 'kind')
        .first()
    )
    if parent is None:
        return {'error': 'parent_not_found'}
    if parent['student_id'] != data['student_id']:
        return {'error': 'parent_other_student'}
    if parent['kind'] not in ('purchase', 'extra'):
        return {'error': 'parent_not_purchase'}

    lessons = int(parent['lessons_count'] or 0)
    blocks = -(-lessons // LESSONS_PER_SUBSCRIPTION)  # ceil
    index = data['subscription_index']
    if not (1 <= index <= blocks):
        return {'error': 'block_out_of_range', 'blocks': blocks}

    obj = Payment.objects.create(
        student_id=data['student_id'],
        direction_id=parent['direction_id'],
        subscriptions_count=None,
        lessons_count=None,
        kind='surcharge',
        parent_payment_id=parent['id'],
        subscription_index=index,
        unit_price=Decimal('0'),
        total_amount=round_kopecks(data['total_amount']),
        paid_at=data['paid_at'],
        note=data.get('note') or None,
        created_by=data.get('created_by') or None,
        created_at=Now(),
    )
    row = _normalize_lessons_count(dictrow(Payment.objects.filter(pk=obj.pk).values()))
    return {'payment': row}


def list_payments(
    student_id: Optional[int] = None,
    direction_id: Optional[int] = None,
    from_: Optional[str] = None,
    to: Optional[str] = None,
) -> list[dict]:
    """
    Список оплат с опциональными фильтрами.

    JOIN students (inner) + LEFT JOIN directions (nullable). ORDER paid_at DESC, id DESC.
    """
    qs = Payment.objects.all()
    if student_id is not None:
        qs = qs.filter(student_id=student_id)
    if direction_id is not None:
        qs = qs.filter(direction_id=direction_id)
    if from_ is not None:
        qs = qs.filter(paid_at__gte=from_)
    if to is not None:
        qs = qs.filter(paid_at__lte=to)

    rows = dictrows(
        qs.order_by('-paid_at', '-id').values(
            *_PAYMENT_FIELDS,
            student_name=F('student__full_name'),
            direction_name=F('direction__name'),
        )
    )
    return [_normalize_lessons_count(r) for r in rows]


def get_payment(payment_id: int) -> Optional[dict]:
    """Возвращает одну оплату или None."""
    row = dictrow(Payment.objects.filter(id=payment_id).values())
    return _normalize_lessons_count(row) if row is not None else None


def delete_payment(payment_id: int) -> dict:
    """
    Хард-удаляет оплату и пересчитывает общий баланс ученика (единый пул).

    Возвращает {'deleted': False} или {'deleted': True, student_id, direction_id, new_balance}.
    """
    row = (
        Payment.objects.filter(id=payment_id)
        .values('student_id', 'direction_id')
        .first()
    )
    if row is None:
        return {'deleted': False}

    Payment.objects.filter(id=payment_id).delete()

    student_id = row['student_id']
    direction_id = row['direction_id']
    balance = _balance_for_student(student_id)
    return {
        'deleted': True,
        'student_id': student_id,
        'direction_id': direction_id,
        'new_balance': balance,
    }


def _balance_for_student(student_id: int) -> int | float:
    """Общий баланс ученика (единый пул). Делегирует в единый дом apps/finances."""
    from apps.finances.repository import balance_for_student
    return balance_for_student(student_id)


def get_student_balance(student_id: int) -> dict:
    """Баланс ученика по всем направлениям. Делегирует в apps/finances."""
    from apps.finances.balance import get_student_balance as _impl
    return _impl(student_id)


def get_direction_payments_count(direction_id: int) -> int:
    """Количество оплат для направления."""
    return Payment.objects.filter(direction_id=direction_id).count()


def _split_refund(remaining_lessons: Decimal, remaining_value: Decimal,
                  by_direction: dict) -> list[tuple]:
    """
    Раскладывает возврат на доли по направлениям: [(direction_id, lessons, value)].

    Доли берутся из непогашенного хвоста FIFO (какие партии реально гасятся), а
    расхождение округления добивается в самую крупную долю — сумма долей ОБЯЗАНА
    сойтись с общим остатком до копейки, иначе баланс не станет нулём.
    """
    parts = [
        (direction_id, b['lessons'], b['value'])
        for direction_id, b in by_direction.items()
        if b['lessons'] > 0
    ]
    if not parts:
        return []

    biggest = max(range(len(parts)), key=lambda i: parts[i][2])
    lessons_gap = remaining_lessons - sum(p[1] for p in parts)
    value_gap = remaining_value - sum(p[2] for p in parts)
    if lessons_gap or value_gap:
        direction_id, lessons, value = parts[biggest]
        parts[biggest] = (direction_id, lessons + lessons_gap, value + value_gap)
    return parts


def refund_student(student_id: int, created_by: str | None = None) -> dict:
    """
    Оформляет возврат неотработанного остатка ученика (единый пул).

    Возвращает {'error': 'student_not_found'|'nothing_to_refund'} или
    {'refunds': [row, ...], 'new_balance': <=0, 'refunded_amount': Decimal}.

    Строк возврата столько, из партий скольких направлений состоит остаток:
    kind='refund', lessons_count/total_amount отрицательные, direction_id — то
    направление, чьи деньги возвращаются. Это не косметика: cap в create_payment
    считает уроки per-direction, и без direction_id на строке возврата лимит курса
    остаётся занятым — вернувшийся ученик не смог бы оплатить тот же курс заново.
    Доплаты сверх курса (kind='extra') лимит не занимали → их доля возвращается
    строкой без направления (см. student_fifo_remaining). Строки иммутабельны, как
    все payments.
    """
    from apps.finances.repository import student_fifo_remaining, balance_for_student
    from apps.students.models import Student

    with transaction.atomic():
        if not Student.objects.select_for_update().filter(id=student_id).exists():
            return {'error': 'student_not_found'}

        rem = student_fifo_remaining(student_id)
        remaining_lessons = rem['remaining_lessons']
        remaining_value = rem['remaining_value']
        if remaining_lessons <= 0 or remaining_value <= 0:
            return {'error': 'nothing_to_refund'}

        parts = _split_refund(
            Decimal(str(remaining_lessons)), remaining_value, rem['remaining_by_direction'],
        )
        created_ids = []
        for direction_id, lessons, value in parts:
            obj = Payment.objects.create(
                student_id=student_id,
                direction_id=direction_id,
                subscriptions_count=None,
                lessons_count=-lessons,
                kind='refund',
                unit_price=Decimal('0'),
                total_amount=-value,
                paid_at=Now(),
                note=f'Возврат {_js_number(lessons)} уроков на сумму {value} ₽',
                created_by=created_by or None,
                created_at=Now(),
            )
            created_ids.append(obj.pk)
        rows = [
            _normalize_lessons_count(r)
            for r in dictrows(Payment.objects.filter(pk__in=created_ids).order_by('id').values())
        ]
        new_balance = balance_for_student(student_id)

    return {'refunds': rows, 'new_balance': new_balance, 'refunded_amount': remaining_value}
