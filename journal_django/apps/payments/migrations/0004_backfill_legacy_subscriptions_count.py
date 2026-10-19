"""
Data-миграция: легаси-оплаты (direction_id NULL, subscriptions_count NULL) получают
subscriptions_count=1 — один абонемент на строку. Безопасно ровно потому, что у ВСЕХ
таких строк unit_price == total_amount (проверено на реальных данных 2026-07-09,
0 исключений из 2176 строк) — условие payments_total_match (total_amount =
unit_price * subscriptions_count) при subscriptions_count=1 выполняется автоматически.
direction_id сознательно остаётся NULL — направление не восстанавливаем (не гадаем).

См. также миграцию 0003 (payments_direction_count_match снят) — БЕЗ неё эта миграция
упадёт по CHECK constraint.
"""
from __future__ import annotations

from django.db import migrations


def backfill_subscriptions_count(apps, schema_editor):
    Payment = apps.get_model('payments', 'Payment')
    Payment.objects.filter(
        direction_id__isnull=True, subscriptions_count__isnull=True,
    ).update(subscriptions_count=1)


def revert_backfill(apps, schema_editor):
    """
    Откат: возвращает subscriptions_count в NULL только для строк, которые сама эта
    миграция и создала — по direction_id IS NULL + created_by='backfill-script'
    (уникальный маркер легаси-бэкафилла, подтверждено: ВСЕ строки с этим created_by
    и NULL direction_id/subscriptions_count принадлежат только этому набору).

    Важно: это НЕ точная логическая инверсия backfill_subscriptions_count (тот
    фильтрует по direction_id/subscriptions_count IS NULL, без учёта created_by).
    Симметрия опирается на эмпирический факт данных на 2026-07-09 (0 исключений
    из 2176 строк), а не на структурный инвариант. Если в будущем легаси-строки
    начнёт писать другой источник (другой created_by) — форвард их заберёт,
    а этот откат не отменит. И наоборот: не трогайте created_by='backfill-script'
    вручную у оплат, не относящихся к этому набору.
    """
    Payment = apps.get_model('payments', 'Payment')
    Payment.objects.filter(
        direction_id__isnull=True, subscriptions_count=1, created_by='backfill-script',
    ).update(subscriptions_count=None)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_remove_direction_count_match_constraint'),
    ]

    operations = [
        migrations.RunPython(backfill_subscriptions_count, revert_backfill),
    ]
