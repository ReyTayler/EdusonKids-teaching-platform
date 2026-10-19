"""
Data-миграция:
  • lessons_count = subscriptions_count * 4 для всех существующих строк
    (subscriptions_count у всех строк проставлен: обычные оплаты + легаси после 0004).
  • kind = 'purchase' для всех существующих строк (страховка; 0005 уже проставила
    default на уровне колонки).
  • created_by = 'Павлов Илья' для ВСЕХ существующих оплат (учётка
    владельца KOTOKOD) — по требованию заказчика.

⚠️ Перезаписывает created_by='backfill-script' (маркер отката 0004). 0004 уже
применена; её обратная миграция при откате должна опираться на direction_id IS NULL
AND subscriptions_count=1, а не на маркер created_by. Форвард 0006 идёт строго после
0004, поэтому здесь маркер уже не нужен.
"""
from __future__ import annotations

from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    Payment = apps.get_model('payments', 'Payment')
    Payment.objects.filter(subscriptions_count__isnull=False, lessons_count__isnull=True) \
        .update(lessons_count=F('subscriptions_count') * 4)
    Payment.objects.update(kind='purchase')
    Payment.objects.update(created_by='Павлов Илья')


def noop_reverse(apps, schema_editor):
    # Необратимо: исходные created_by не восстанавливаем.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0005_add_lessons_count_kind'),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
