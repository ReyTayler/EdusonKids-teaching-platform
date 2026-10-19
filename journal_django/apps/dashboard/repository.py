"""
DashboardRepository — данные дашборда (поступления по периоду и по дням).
FIFO — через apps/finances.

ORM-порт services/repo/dashboard.js (раздел 09). Сам FIFO-движок и загрузка
партий/посещений живут в apps/finances (не дублируем).

Полуинтервалы дат [period_start, period_end) сохранены (__gte + __lt).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import (
    Avg, Case, Count, DecimalField, ExpressionWrapper, F, Min, Q, Sum, Value, When,
)
from django.db.models.functions import Cast, Coalesce

from apps.payments.models import Payment


_ZERO = Value(Decimal('0'), output_field=DecimalField(max_digits=20, decimal_places=2))
# Денежный/дробный тип для выражений: срок в месяцах бывает дробным (1 урок = 0.25).
_MONEY = DecimalField(max_digits=20, decimal_places=4)


def revenue_for_period(period_start: str, period_end: str):
    """
    SUM(total_amount) в полуинтервале [period_start, period_end), БЕЗ возвратов.
    Возвращает Decimal.

    Возвраты (kind='refund', суммы отрицательные) исключены по решению
    пользователя 2026-10-05: дашборд показывает поступления. Строки возврата
    по-прежнему видны в разделе «Отчёты» и в карточке ученика. Это
    единственный вид с отрицательной суммой (CHECK в БД), поэтому после
    исключения в выборке остаются только неотрицательные суммы.
    """
    return Payment.objects.filter(
        paid_at__gte=period_start, paid_at__lt=period_end,
    ).exclude(kind='refund').aggregate(total=Coalesce(Sum('total_amount'), _ZERO))['total']


def revenue_by_day(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """
    Поступления по дням paid_at в [date_from, date_to] (включительно): сумма
    покупок, доплат к абонементу и доп.уроков и число заказов — оплат с
    total_amount > 0 (строка на 0 ₽ заказом не считается). Возвраты исключены,
    как в revenue_for_period. Только дни, где были оплаты; ASC по дате.
    Индекс payments_paid_at_idx.
    """
    return list(
        Payment.objects
        .filter(paid_at__gte=date_from, paid_at__lte=date_to)
        .exclude(kind='refund')
        .values('paid_at')
        .annotate(
            rev=Coalesce(Sum('total_amount'), _ZERO),
            orders=Count('id', filter=Q(total_amount__gt=0)),
        )
        .order_by('paid_at')
    )


# Срок заказа в месяцах. Абонемент = месяц (4 урока), поэтому где
# subscriptions_count не проставлен (поштучные покупки 1-3 уроков, доп.уроки) —
# считаем уроки: 1 урок = 0.25 месяца (решение пользователя 2026-10-07).
# Строки совсем без срока (доплата к абонементу, kind='surcharge') дают NULL и
# в Months paid / ARPM не участвуют — у доплаты своего срока нет.
_MONTHS = Case(
    When(subscriptions_count__gt=0, then=Cast('subscriptions_count', _MONEY)),
    When(lessons_count__gt=0, then=F('lessons_count') / Value(Decimal('4'))),
    default=Value(None),
    output_field=_MONEY,
)

# Заказ со сроком — строка, у которой _MONTHS не NULL (делить есть на что).
_HAS_MONTHS = Q(subscriptions_count__gt=0) | Q(lessons_count__gt=0)

# Покупка курса: сама продажа, без доплат к абонементу и доп.уроков.
_IS_PURCHASE = Q(kind='purchase', total_amount__gt=0)


def revenue_by_direction(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """
    Сводка по курсам за [date_from, date_to] (включительно), по направлению оплаты.

    На строку: purchases — число покупок курса; months — оплачено месяцев;
    revenue — ВСЕ поступления направления (покупки + доплаты к абонементу +
    доп.уроки, возвраты исключены); arpm — средняя цена месяца, посчитанная как
    СРЕДНЕЕ ПО ЗАКАЗАМ отношения «цена заказа ÷ его срок» (не revenue/months:
    на боевом примере пользователя это разные числа).

    direction_id = None — легаси-оплаты без направления, отдельной строкой.
    ASP (revenue/purchases) считает сервис: он же округляет.
    """
    rows = (
        Payment.objects
        .filter(paid_at__gte=date_from, paid_at__lte=date_to)
        .exclude(kind='refund')
        .values('direction_id', 'direction__name')
        .annotate(
            revenue=Coalesce(Sum('total_amount'), _ZERO),
            purchases=Count('id', filter=_IS_PURCHASE),
            months=Sum(_MONTHS, filter=_IS_PURCHASE),
            arpm=Avg(
                ExpressionWrapper(F('total_amount') / _MONTHS, output_field=_MONEY),
                filter=_IS_PURCHASE & _HAS_MONTHS,
            ),
        )
        .order_by('-revenue')
    )
    return list(rows)


def first_payment_date():
    """Самая ранняя paid_at в базе (date) или None, если оплат нет."""
    return Payment.objects.aggregate(d=Min('paid_at'))['d']
