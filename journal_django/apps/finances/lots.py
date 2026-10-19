"""
Построение партий (lots) FIFO из строк оплат — ЕДИНСТВЕННОЕ место этой логики.

Партия — набор уроков с одной ценой. По умолчанию партия = вся оплата
(цена = сумма / уроки), как было исторически. Если у оплаты есть доплаты к
абонементам (kind='surcharge'), она дробится на блоки по 4 урока: доплата
поднимает цену ТОЛЬКО своего блока. См.
docs/superpowers/specs/2026-07-28-course-surcharge-design.md.

Чистые функции без ORM: вызывающий (apps/finances/repository.py) подаёт строки и
словарь доплат, здесь только арифметика. Decimal везде — деньги считаются точно.
"""
from __future__ import annotations

from decimal import Decimal

# Абонемент = 4 урока. Та же величина, что LESSONS_PER_MONTH в revenue_forecast,
# но смысл другой (там — темп занятий), поэтому константа своя.
LESSONS_PER_SUBSCRIPTION = 4


def build_lots(rows, surcharges_by_parent):
    """
    rows: оплаты В ПОРЯДКЕ FIFO (paid_at, id), каждая —
          {'id', 'lessons_count', 'total_amount', 'kind', 'direction_id'}.
          Строки kind='refund' вызывающий сюда НЕ подаёт (они не партии, а
          синтетические списания).
    surcharges_by_parent: {parent_payment_id: {subscription_index: Decimal сумма}}.

    Возвращает [{'lessons': int, 'price_per_lesson': Decimal, 'direction_id': int|None,
                 'payment_id': int}]. payment_id — id оплаты, породившей партию;
    оплата с доплатами даёт несколько партий с ОДНИМ payment_id (по нему реестр
    признания выручки собирает их обратно в один платёж).
    """
    lots = []
    for r in rows:
        raw = r['lessons_count']
        lessons = int(raw) if raw is not None else 0
        if lessons <= 0:
            continue  # guard: NULL/0 — деление на ноль ломает суммы
        # Направление партии: доплата сверх курса (kind='extra') лимит не занимала,
        # поэтому её остаток не привязан к направлению (прежнее правило).
        direction_id = None if r['kind'] == 'extra' else r['direction_id']
        total = Decimal(r['total_amount'])
        surcharges = surcharges_by_parent.get(r['id']) or {}
        if not surcharges:
            lots.append({
                'lessons': lessons,
                'price_per_lesson': total / Decimal(lessons),
                'direction_id': direction_id,
                'payment_id': r['id'],
            })
            continue
        lots.extend(_split_into_blocks(lessons, total, surcharges, direction_id, r['id']))
    return lots


def _split_into_blocks(lessons: int, total: Decimal, surcharges: dict, direction_id, payment_id):
    """
    Разрезать оплату на абонементы по 4 урока и раздать доплаты по номерам блоков.

    Все блоки, кроме последнего: base_k = total * lessons_k / lessons_count (доля
    блока в сумме оплаты, п. 4.2 спеки). `total/lessons_count` почти всегда
    периодическая дробь (например 44000/36), поэтому Decimal-деление округляет её
    на 28-й значащей цифре — на одном блоке это незаметно, но 9 округлений подряд
    «утекают» от total на последнюю цифру. Последний блок абсорбирует остаток:
    base_last = total − Σ(base предыдущих), что телескопически восстанавливает
    total день-в-день независимо от округления промежуточных блоков — это и даёт
    «сумма долей равна сумме оплаты точно» (п. 4.2) без округления цены урока.
    Doплаты (extra) добавляются к base ПОСЛЕ и в этот остаток не входят — они уже
    точные суммы, деления не требуют.

    Последний блок может быть неполным (оплата некратного 4 числа уроков, доплата сверх курса).
    Доплаты с номерами больше числа блоков игнорируются: битые данные не должны
    ронять расчёт всей школы.
    """
    block_sizes = []
    remaining = lessons
    while remaining > 0:
        block_sizes.append(min(LESSONS_PER_SUBSCRIPTION, remaining))
        remaining -= block_sizes[-1]

    out = []
    base_sum = Decimal('0')
    last = len(block_sizes) - 1
    for i, block_lessons in enumerate(block_sizes):
        index = i + 1
        if i < last:
            base = total * Decimal(block_lessons) / Decimal(lessons)
        else:
            base = total - base_sum  # остаток — точная сумма без деления
        base_sum += base
        extra = surcharges.get(index, Decimal('0'))
        out.append({
            'lessons': block_lessons,
            'price_per_lesson': (base + extra) / Decimal(block_lessons),
            'direction_id': direction_id,
            'payment_id': payment_id,
        })
    return out
