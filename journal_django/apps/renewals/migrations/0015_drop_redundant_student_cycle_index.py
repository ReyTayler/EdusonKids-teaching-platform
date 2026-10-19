"""Убрать renewal_deal_student_cycle_idx — он дублирует UNIQUE-констрейнт.

Индекс добавлялся в 0013 под подзапрос «последняя сделка ученика» (аннотация
стадии в apps/students/repository.py). Оказалось, что он не нужен:
renewal_deal_student_cycle_uq — UNIQUE(student_id, cycle_no) — это тот же btree
по тому же префиксу, и `ORDER BY cycle_no DESC LIMIT 1` идёт по нему обратным
сканом с тем же планом (проверено EXPLAIN ANALYZE на dev-БД: Index Scan Backward,
одна строка на ученика). Второй btree давал только лишнюю запись на каждый
INSERT/UPDATE сделки — на VPS 2 CPU это не бесплатно.

UNIQUE-констрейнт снять нельзя: «одна сделка на ученика × цикл» — доменный
инвариант, так что подстраховка отдельным индексом не требуется.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('renewals', '0014_remove_renewaldeal_insert_insert_and_more'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='renewaldeal',
            name='renewal_deal_student_cycle_idx',
        ),
    ]
